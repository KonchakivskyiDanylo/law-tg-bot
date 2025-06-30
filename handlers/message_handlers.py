"""
Message handlers for the Legal Support Telegram Bot with integrated legal query processing.
"""

import os
import aiofiles
import logging
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackContext, ContextTypes
from telegram.constants import ParseMode
from services.openai_service import handle_legal_query, get_legal_term_definition
from handlers.command_handlers import get_main_menu, get_back_to_menu_button, get_post_document_buttons
from services.subscription_service import start_new_request_session, append_to_last_request_dialog, \
    get_conversation_history, has_accepted_agreement, update_last_session_rating
from services.integrated_document_generator import process_user_message_integrated

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def handle_message(update: Update, context: CallbackContext) -> None:
    """
    Handles incoming messages and user interactions with the bot. This function is designed to manage different
    states of interaction based on user behavior such as agreement handling, awaiting user rating, document
    clarifications, or document handling. It appropriately routes actions depending on the type of input received
    (e.g., text, document, or undefined) and current user state.

    :param update: The incoming update payload containing user interaction details.
    :type update: Update
    :param context: The context of the callback function holding private state data per user in the bot's session.
    :type context: CallbackContext
    :return: None
    :rtype: None
    """
    user = update.effective_user
    user_data = context.user_data

    user_id = str(user.id)

    if not has_accepted_agreement(user_id):
        await update.message.reply_text("Please accept the data processing agreement to use the bot.")
        return

    if context.user_data.get("awaiting_rating"):
        user_rating = update.message.text
        logger.info(f"Rating from user {update.effective_user.id}: {user_rating}")
        update_last_session_rating(user_id, user_rating)
        await update.message.reply_text("✅ Thank you for your rating!", reply_markup=get_back_to_menu_button())
        context.user_data["awaiting_rating"] = False
        return

    if user_data.get('awaiting_document_clarification'):
        await handle_document_clarification(update, context)
        return

    if user_data.get('awaiting_document'):
        await handle_document_input(update, context)
        return

    if update.message.document:
        logger.debug("Received a document from user.")
        await handle_document_input(update, context)
        return

    if update.message.text:
        user_text = update.message.text
        logger.debug(f"Received text message: {user_text}")
        await handle_message_input(update, context)
        return

    await update.message.reply_text(
        "🤖 I don't understand you. Please select an action from the menu.",
        reply_markup=get_main_menu(str(user.id))
    )


async def handle_document_clarification(update: Update, context: CallbackContext) -> None:
    """
    Handles additional clarification or information provided by the user regarding a document.
    This function manages the conversation state and processes the new input provided
    by the user within the ongoing document session. It also determines whether further
    clarifications are needed or the document generation process can be finalized.

    The function implements conversation history tracking, calls an underlying process to handle
    the input, manages bot communication actions, and appropriately updates the user session data
    to reflect the current state of the interaction. It ensures that any generated document is sent
    back to the user and maintains a complete record of the ongoing request.

    :param update: Instance of :class:`telegram.Update` representing update information about
        the latest interaction including user details and the message sent.
    :param context: Instance of :class:`telegram.ext.CallbackContext` providing context for
        the ongoing update, including user session data and bot interaction objects.
    :return: None
    """
    try:
        user = update.effective_user
        chat_id = str(user.id)
        additional_info = update.message.text

        logger.info(f"Received additional document information from user {chat_id}")
        document_session = context.user_data.get('document_session', {})
        original_message = document_session.get('original_message', '')

        conversation_messages = document_session.get('conversation_messages', [])
        conversation_messages.append({"role": "user", "content": additional_info})

        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        waiting_message = await update.message.reply_text("⏳ Processing additional information...")

        response_text, file_path = await process_user_message_integrated(
            message=f"{original_message}\n\nAdditional information: {additional_info}",
            chat_id=int(chat_id),
            bot=context.bot,
            conversation_messages=conversation_messages
        )

        await waiting_message.delete()

        if "additional information" in response_text.lower() or "please clarify" in response_text.lower():
            markup = get_back_to_menu_button()
            context.user_data['document_session']['conversation_messages'] = conversation_messages
        else:
            markup = get_post_document_buttons() if file_path and os.path.exists(
                file_path) else get_back_to_menu_button()
            context.user_data['awaiting_document_clarification'] = False
            context.user_data.pop('document_session', None)

        if len(response_text) > 4000:
            chunks = [response_text[i:i + 4000] for i in range(0, len(response_text), 4000)]
            for i, chunk in enumerate(chunks):
                if i == 0:
                    await update.message.reply_text(f"{chunk}\n\n(Part {i + 1}/{len(chunks)})", reply_markup=markup)
                else:
                    await update.message.reply_text(f"{chunk}\n\n(Part {i + 1}/{len(chunks)})")
        else:
            await update.message.reply_text(response_text, reply_markup=markup)

        if file_path and os.path.exists(file_path):
            with open(file_path, "rb") as doc:
                await update.message.reply_document(document=doc)
            try:
                os.remove(file_path)
            except Exception:
                logger.warning(f"Failed to remove file after sending: {file_path}")

        user_id = str(chat_id)
        if "current_request" not in context.user_data:
            start_new_request_session(user_id, additional_info[:3000], "document")
            context.user_data["current_request"] = "document"
        else:
            append_to_last_request_dialog(user_id, "user", additional_info[:3000])
        append_to_last_request_dialog(user_id, "bot", response_text)

    except Exception as e:
        logger.error(f"Error in handle_document_clarification: {e}", exc_info=True)
        await update.message.reply_text(
            "⚠️ An error occurred while processing the additional information. Please try again later.",
            reply_markup=get_back_to_menu_button()
        )
        context.user_data['awaiting_document_clarification'] = False
        context.user_data.pop('document_session', None)


async def handle_message_input(update: Update, context: CallbackContext) -> None:
    """
    Handles user input via message updates in a chatbot context. Sends user queries
    to the appropriate processing pipelines for further analysis and generates an
    appropriate response based on the query contents.

    This function evaluates the user's input, determines if it matches criteria for
    specific handling (e.g., asking for legal term definitions), and communicates
    with APIs or services to return an appropriate response to the user. The response
    is appended to the conversation context and provides functionality for creating
    documents from the chatbot's response.

    :param update: Object containing details of the incoming Telegram update. It includes
        information about the user and the message sent by the user.
    :type update: Update
    :param context: Object providing contextual information for handling the user request.
        It includes user-specific data and access to the bot API for responding to the user.
    :type context: CallbackContext
    :return: This coroutine does not return a value but uses the Telegram bot API to
        respond to the user based on their input.
    :rtype: None
    """
    try:
        user = update.effective_user
        chat_id = str(user.id)
        question = update.message.text

        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

        if "current_request" not in context.user_data:
            context.user_data['current_request'] = True
            start_new_request_session(chat_id, question, "message")
        else:
            append_to_last_request_dialog(chat_id, "user", question)

        analyzing_msg = await update.message.reply_text("thinking💭", reply_markup=get_back_to_menu_button())

        term_match = re.search(r'(?:definition|what is|define|term)\s+["\']?([^"\'?]+)["\']?', question.lower())
        if term_match:
            term = term_match.group(1).strip()
            definition = get_legal_term_definition(term)
            await analyzing_msg.delete()
            try:
                await update.message.reply_text(definition, parse_mode=ParseMode.MARKDOWN,
                                                reply_markup=get_back_to_menu_button())
            except Exception:
                await update.message.reply_text(definition, reply_markup=get_back_to_menu_button())
            append_to_last_request_dialog(chat_id, "bot", definition)
            return

        history = get_conversation_history(chat_id)
        result = await handle_legal_query(query=question, conversation_history=history)
        response_text = result.get("response_text", "Sorry, your request could not be processed.")

        await analyzing_msg.delete()
        context.user_data['last_model_response'] = response_text

        keyboard = [
            [InlineKeyboardButton("📄 Create Document", callback_data="create_document_from_response")],
            [InlineKeyboardButton("🏠 Menu", callback_data="back_to_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await update.message.reply_text(response_text, parse_mode=ParseMode.MARKDOWN,
                                            reply_markup=reply_markup)
        except Exception:
            await update.message.reply_text(
                "An error occurred while formatting the response. Here is the plain version:\n\n" + response_text,
                reply_markup=reply_markup)

        append_to_last_request_dialog(chat_id, "bot", response_text)

    except Exception as e:
        logger.error(f"Error in handle_message_input: {e}", exc_info=True)
        try:
            await update.message.reply_text("⚠️ A technical error occurred. Please try again later.",
                                            reply_markup=get_back_to_menu_button())
        except Exception:
            pass


async def handle_document_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles user input of either plain text or document files, processes the input, and responds
    with analyzed output. Supports only specific file formats and imposes size limits on uploaded
    files and input content. Text files are read and processed, while unsupported or corrupted
    files are handled with appropriate feedback to the user.

    This function also integrates document context into user queries, truncates overly long inputs,
    starts or appends to request sessions, and provides an interactive response mechanism based on
    the processed user input.

    :param update: The Update object representing the Telegram update event, including user messages
        and chat details.
    :type update: telegram.Update
    :param context: The Context object provided by the Telegram application, which manages the bot
        state and user interactions.
    :type context: telegram.ext.ContextTypes.DEFAULT_TYPE
    :return: None
    """
    chat_id = update.effective_chat.id
    message = update.message
    user_input = None

    logger.info(f"User {chat_id} is uploading document or text")

    document_context = context.user_data.get('document_context', '')
    if document_context:
        user_input = f"Context from the previous response:\n{document_context}\n\n" + (message.text or "")

    if message.text:
        user_input = user_input if user_input else message.text.strip()

    elif message.document:
        document = message.document
        file_name = document.file_name
        file_size = document.file_size
        file = await context.bot.get_file(document.file_id)

        if file_size and file_size > 20 * 1024 * 1024:
            await message.reply_text("⚠️ File size exceeds 20 MB. Please upload a smaller file.")
            return

        if not file_name.endswith((".txt", ".md", ".docx", ".pdf")):
            await message.reply_text("⚠️ Only text files are supported (.txt, .md, .docx, .pdf).")
            return

        import tempfile
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, file_name)
        await file.download_to_drive(temp_path)

        try:
            if file_name.endswith((".txt", ".md")):
                async with aiofiles.open(temp_path, "r", encoding="utf-8") as f:
                    user_input = await f.read()
            elif file_name.endswith(".docx"):
                from docx import Document
                doc = Document(temp_path)
                user_input = "\n".join(para.text for para in doc.paragraphs)
            elif file_name.endswith(".pdf"):
                try:
                    from pypdf import PdfReader
                    pdf_reader = PdfReader(temp_path)
                    text_content = []
                    for page in pdf_reader.pages:
                        text_content.append(page.extract_text() or "")
                    user_input = "\n".join(text_content)
                except ImportError:
                    logger.error("pypdf library not installed")
                    await message.reply_text("⚠️ The pypdf library is required to process PDF files.")
                    return
                except Exception as pdf_error:
                    logger.error(f"Error reading PDF file {file_name}: {pdf_error}", exc_info=True)
                    await message.reply_text("⚠️ Could not read the PDF file. Make sure it's not corrupted.")
                    return
        except UnicodeDecodeError:
            try:
                if file_name.endswith((".txt", ".md")):
                    async with aiofiles.open(temp_path, "r", encoding="latin-1") as f:
                        user_input = await f.read()
                else:
                    raise Exception("Not a text file with encoding issues")
            except Exception as enc_error:
                logger.error(f"Error with encoding for file {file_name}: {enc_error}", exc_info=True)
                await message.reply_text("⚠️ File encoding issue. Please save the file in UTF-8 format.")
                return
        except Exception as e:
            logger.error(f"Error reading uploaded document {file_name}: {e}", exc_info=True)
            await message.reply_text("⚠️ Could not read the file. Make sure it's not corrupted.")
            return

        if user_input and len(user_input) > 500000:
            await message.reply_text("⚠️ File content is too large. Please upload a smaller text file.")
            return

    if not user_input:
        await message.reply_text("⚠️ Please send text or a valid text file.")
        return

    if len(user_input) > 50000:
        user_input = user_input[:50000]
        await message.reply_text("⚠️ Text was truncated to 50,000 characters due to size limitations.")

    try:
        if "current_request" not in context.user_data:
            start_new_request_session(str(chat_id), user_input[:3000], "document")
            context.user_data["current_request"] = "document"
        else:
            append_to_last_request_dialog(str(chat_id), "user", user_input[:3000])

        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        waiting_message = await message.reply_text("⏳ Analyzing the document and generating a response...")

        response_text, file_path = await process_user_message_integrated(
            message=user_input,
            chat_id=chat_id,
            bot=context.bot
        )

        await waiting_message.delete()

        if ("additional information" in response_text.lower() or
                "please clarify" in response_text.lower() or
                "more details required" in response_text.lower()):
            context.user_data['awaiting_document_clarification'] = True
            context.user_data['document_session'] = {
                'original_message': user_input,
                'document_type': 'undefined',
                'conversation_messages': [{"role": "user", "content": user_input}]
            }
            markup = get_back_to_menu_button()
        else:
            markup = get_post_document_buttons() if file_path and os.path.exists(
                file_path) else get_back_to_menu_button()
            context.user_data['awaiting_document'] = False

        if len(response_text) > 4000:
            chunks = [response_text[i:i + 4000] for i in range(0, len(response_text), 4000)]
            for i, chunk in enumerate(chunks):
                if i == 0:
                    await message.reply_text(f"{chunk}\n\n(Part {i + 1}/{len(chunks)})", reply_markup=markup)
                else:
                    await message.reply_text(f"{chunk}\n\n(Part {i + 1}/{len(chunks)})")
        else:
            await message.reply_text(response_text, reply_markup=markup)

        if file_path and os.path.exists(file_path):
            try:
                with open(file_path, "rb") as doc:
                    await message.reply_document(document=doc)
                os.remove(file_path)
            except Exception as file_error:
                logger.error(f"Error sending document file: {file_error}")
                await message.reply_text("⚠️ The document was created, but the file could not be sent.")

        append_to_last_request_dialog(str(chat_id), "bot", response_text)

    except Exception as e:
        logger.error(f"Error in handle_document_input: {e}", exc_info=True)
        await message.reply_text(
            "⚠️ An error occurred while processing the document. Please try again later.",
            reply_markup=get_back_to_menu_button()
        )
        context.user_data['awaiting_document'] = False


async def get_document_type_gpt(message: str, client) -> str:
    """
    Asynchronously determines the type of legal document inferred from a given message.
    The user provides a textual message describing their need for a legal document,
    and this function processes the response from the client to classify the document
    into one of the predefined categories: contract, application, act, claim,
    power_of_attorney, complaint, notice, demand, receipt, or letter. If the document type
    cannot be classified, "undefined" is returned instead.

    :param message: A user-provided textual description specifying the type of legal
        document they wish to create.
    :type message: str
    :param client: The client object used to communicate with an AI model to process
        the input message and return the corresponding document type.
    :type client: Any
    :return: The inferred document type, categorized as one of the predefined legal
        document classifications, or "undefined" if no classification can be made.
    :rtype: str
    """
    system_prompt = (
        "You are a legal assistant. The user is sending a message requesting to create a legal document.\n"
        "Based on their request, identify one of the following document types:\n"
        "- contract\n"
        "- application\n"
        "- act\n"
        "- claim\n"
        "- power_of_attorney\n"
        "- complaint\n"
        "- notice\n"
        "- demand\n"
        "- receipt\n"
        "- letter\n"
        "Respond strictly in the format: TYPE: <name>\n"
        "Example: TYPE: contract"
    )

    response = await client.chat.completions.create(
        model="o3-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message}
        ],
        temperature=0
    )

    reply = response.choices[0].message.content.strip().lower()
    match = re.search(r"type:\s*(\w+)", reply)

    if match:
        return match.group(1)
    return "undefined"
