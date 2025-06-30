"""
Command handlers for the Legal Support Telegram Bot.
Handles start/menu commands, subscriptions, payment, question/document input, and history sessions.
"""
from services.payment_monitor import add_payment_to_monitor, check_user_payments, get_payment_monitor
import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import CallbackContext
from repositories.user_repository import save_user, get_user_by_id
from services.subscription_service import (
    update_subscription, update_payment_method,
    get_user_sessions_summary, delete_user_history, move_session_to_end,
    has_basic_subscription, has_premium_subscription, has_few_chats_last_30_days, delete_last_session
)
from services.yookassa_service import create_payment, check_payment_status
from config.config import TARIFF_PRICES, TIMEZONE_OFFSET_HOURS
from prompts import PAYMENT_FAILURE_MESSAGE

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

MONTHS = {
    1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
    7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December"
}


def format_date(date_str):
    """
    Formats a given date string into a specific format.

    The function takes a string representing a date in the format
    YYYY-MM-DD and returns a formatted string representing the date in
    the format "DD Month YYYY года". If the input string does not match
    the expected date format or an issue occurs during formatting, the
    input string is returned unchanged.

    :param date_str: The date string to be formatted, expected in
        the format YYYY-MM-DD.
    :type date_str: str
    :return: A string representing the formatted date in "DD Month YYYY года";
        if the input date string is invalid, it returns the original string.
    :rtype: str
    """
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{date_obj.day} {MONTHS[date_obj.month]} {date_obj.year} year"
    except Exception:
        return date_str


def format_timestamp(timestamp_str: str, offset_hours: int = TIMEZONE_OFFSET_HOURS) -> str:
    """
    Formats a given ISO 8601 timestamp string to a specific time format based on a
    provided timezone offset. The function adjusts the given timestamp by the
    offset in hours and returns the formatted timestamp in the format
    `DD.MM.YYYY HH:MM`. If any exception occurs during processing, it will return
    a string indicating an unknown date.

    :param timestamp_str: The ISO 8601 formatted string representing the timestamp
        to be adjusted.
    :param offset_hours: The timezone offset, in hours, to adjust the provided
        timestamp. Defaults to the value defined by the constant
        `TIMEZONE_OFFSET_HOURS`.
    :return: A string representing the adjusted and formatted timestamp in the
        `DD.MM.YYYY HH:MM` format, or the string "Unknown Date" if an error occurs
        during processing.
    :rtype: str
    """
    try:
        dt = datetime.fromisoformat(timestamp_str) + timedelta(hours=offset_hours)
        return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return "Unknown Date"


async def delete_previous_message(update: Update):
    """
    Asynchronously deletes the previous message associated with the callback query
    in the provided update object. If the provided update contains a callback
    query, it attempts to delete the associated message. Logs a warning if the
    deletion fails.

    :param update: The update object containing the callback query and its
     associated message.
    :type update: Update
    :return: None
    """
    if hasattr(update, "callback_query") and update.callback_query:
        try:
            await update.callback_query.message.delete()
        except Exception as e:
            logger.warning(f"Failed to delete previous message: {e}")


def get_back_to_menu_button():
    """
    Constructs and returns a button that allows users to navigate back to the main menu
    within a Telegram bot interface. The button is created using an inline keyboard
    markup with a single "🏠 Menu" button.

    :return: Inline keyboard markup with a "Back to Menu" button
    :rtype: InlineKeyboardMarkup
    """
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="back_to_menu")]])


def get_rate_button():
    """
    Generates a list containing a single InlineKeyboardButton instance for user
    interaction.

    The function creates an inline button, typically used in Telegram bots,
    with the label "📝Technical Support" and a specific callback data of
    "rate_document".

    :return: A list containing one InlineKeyboardButton object.
    :rtype: list[InlineKeyboardButton]
    """
    return [InlineKeyboardButton("📝Technical Support", callback_data="rate_document")]


def get_post_document_buttons():
    """
    Generates an inline keyboard markup containing buttons for navigating to the
    main menu or rating a document.

    The function creates a keyboard with two buttons. The first button allows
    the user to return to the main menu, while the second one facilitates
    document rating functionality. The keyboard is intended for Telegram bots
    using the Telegram InlineKeyboardMarkup feature.

    :returns: Inline keyboard markup containing the 'Menu' and 'Rate Document'
        buttons.
    :rtype: InlineKeyboardMarkup
    """
    keyboard = [
        [InlineKeyboardButton("🏠 Menu", callback_data="back_to_menu")],
        [InlineKeyboardButton("📝 Rate Document", callback_data="rate_document")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_main_menu(user_id: str) -> InlineKeyboardMarkup:
    """
    Constructs the main menu as an inline keyboard based on the user's subscription
    status.

    The function checks the subscription status of a user to determine the set of
    actions that should be included in the keyboard. Users with no active subscription
    are presented with an additional option to subscribe.

    :param user_id: Identifier of the user to retrieve subscription status and
        customize the menu.
    :type user_id: str
    :return: Inline keyboard markup object containing buttons for actions in the
        main menu.
    :rtype: InlineKeyboardMarkup
    """
    has_premium = has_premium_subscription(user_id)
    has_basic = has_basic_subscription(user_id)

    keyboard = [
        [InlineKeyboardButton("❓ Consultation", callback_data="main_ask")],
        [InlineKeyboardButton("📄 Document Preparation", callback_data="main_document")],
        [InlineKeyboardButton("📜 Request History", callback_data="main_history")],
        [InlineKeyboardButton("💼 Manage Subscription", callback_data="main_my_subscription")],
        [InlineKeyboardButton("📝 Technical Support", callback_data="rate_document")]
    ]

    if not has_premium and not has_basic:
        keyboard.append([InlineKeyboardButton("💰 Subscribe", callback_data="main_new_subscription")])

    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: CallbackContext) -> None:
    """
    Handles the /start command initiated by the user. Logs the user's action
    and processes the initialization logic. Ensures the user meets certain
    conditions, such as being registered, and directs them accordingly. If
    the user is not registered, presents a code of conduct message with an
    interactive button. If registered, displays the main menu interface.

    :param update: The incoming update that triggers the command.
        Contains data about the user and the message content.
    :type update: Update
    :param context: Provides contextual information and utilities for handling the
        update, such as bot data, user data, and application data.
    :type context: CallbackContext
    :return: None
    :rtype: None
    """
    user = update.effective_user
    user_id = str(user.id)

    logger.info(f"User {user_id} triggered /start")

    if not (update.message and update.message.text == "/start"):
        return

    user_inf = get_user_by_id(user_id)
    if not user_inf:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Accept", callback_data="accept_code")]
        ])
        from prompts import CODE_OF_CONDUCT
        await update.message.reply_text(CODE_OF_CONDUCT, reply_markup=keyboard)
        return

    await show_main_menu(update, context)


async def handle_accept_code(update: Update, context: CallbackContext) -> None:
    """
    Handles the "accept_code" event triggered by a callback query. This function processes
    the user's action to accept a code agreement, logs their information, and subsequently
    updates necessary components like the main menu display.

    :param update:
        Telegram update object received from the bot server. Contains
        the callback query and user information.
    :type update: Update
    :param context:
        Context object containing the bot, job queue, and other functionality
        associated with the bot.
    :type context: CallbackContext

    :return:
        None
    """
    query = update.callback_query
    if not query or query.data != "accept_code":
        return

    await query.answer()
    user = update.effective_user
    user_id = str(user.id)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user_info = {
        "_id": user_id,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "username": user.username,
        "agreement_time": now_str,
        "subscription_active": False,
        "subscription_info": {"type": "", "start": "", "end": ""},
        "payment_method_id": "",
        "previous_requests": []
    }
    save_user(user_info)
    await delete_previous_message(update)
    await show_main_menu(update, context)


async def menu_command(update: Update, context: CallbackContext) -> None:
    """
    Handles the invocation of the main menu command. This function resets certain user data states
    to ensure the main menu is presented in a clean state. It also removes previous related content
    to prepare for a fresh interaction and displays the main menu to the user.

    :param update: Represents an incoming update. Contains user-specific data such as their ID and
        the current state of the ongoing session.
    :type update: Update
    :param context: Provides contextual information for the callback. Includes data relevant
        to the execution scope, like user-specific settings and ongoing session state.
    :type context: CallbackContext
    :return: None
    """
    logger.info(f"User {update.effective_user.id} opened main menu")
    await delete_previous_message(update)
    context.user_data["awaiting_document"] = False
    context.user_data["awaiting_document_clarification"] = False
    context.user_data.pop("document_session", None)
    context.user_data.pop("document_context", None)
    context.user_data.pop("last_model_response", None)
    await show_main_menu(update, context)


async def show_main_menu(update: Update, context: CallbackContext):
    """
    Displays the main menu to the user. Depending on the type of update
    (either a message or a callback query), it sends the appropriate
    reply with the main menu text and corresponding inline keyboard.
    The menu text includes a personalized greeting if the user's first
    name is available.

    The function works asynchronously, awaiting message replies when
    sending information to the user.

    :param update: Contains information and data regarding the incoming update
        (either a message or callback query).
    :type update: Update
    :param context: Provides context and data related to the running
        conversation, as well as utilities for bot operations.
    :type context: CallbackContext
    :return: None, but sends a message or a reply to the user containing
        the main menu and inline keyboard.
    """
    user = update.effective_user
    greeting = f"Hello, {user.first_name}!" if user and user.first_name else "Hello!"
    from prompts import MENU_TEXT
    text = MENU_TEXT.format(greeting=greeting)

    if hasattr(update, "message") and update.message:
        await update.message.reply_text(text, reply_markup=get_main_menu(str(user.id)))
    elif hasattr(update, "callback_query") and update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=get_main_menu(str(user.id)))


async def handle_ask(update: Update, context: CallbackContext):
    """
    Handles the "Ask a Question" interaction initiated by a user. Responds to the user's
    request based on their subscription status or activity and updates the chat accordingly.

    :param update: The update object containing information about the incoming update.
        Represents an incoming update in the bot's interaction with a user.
    :type update: Update
    :param context: Provides contextual information and methods for managing the bot's behavior
        within the interaction, such as accessing data or sending replies.
    :type context: CallbackContext
    :return: No explicit return value, but the function modifies the chat state or responds
        based on the user's subscription status and activity.
    :rtype: None
    """
    from prompts import ASK_PROMPT, LIMIT_REACHED_PROMPT
    logger.info(f"User {update.effective_user.id} requested to ask a question")
    await delete_previous_message(update)
    user_id = str(update.effective_user.id)
    query = update.callback_query

    if not (has_premium_subscription(user_id) or has_basic_subscription(user_id) or has_few_chats_last_30_days(
            user_id)):
        await query.message.reply_text(LIMIT_REACHED_PROMPT, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Subscribe", callback_data="main_new_subscription")],
            [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")]
        ]))
        return

    await query.answer()
    await query.message.reply_text(ASK_PROMPT, reply_markup=get_back_to_menu_button())


async def handle_create_document(update: Update, context: CallbackContext):
    """
    Handles the creation of a document for the requesting user. This function checks if the user has a premium subscription
    and provides appropriate prompts for creating a document or redirects them to subscription options if they do not have
    the required access. The function operates asynchronously and interacts with the relevant Telegram user interface
    components for message handling and navigation.

    :param update: Represents an incoming update in the Telegram bot context, containing information about the interaction
                   that triggered this function.
                   :type update: Update
    :param context: Contains contextual information for the Telegram bot interaction, such as user data and current update
                    state.
                    :type context: CallbackContext
    :return: None
    """
    from prompts import DOCUMENT_PROMPT, NO_PREMIUM_DOCUMENT_PROMPT
    logger.info(f"User {update.effective_user.id} requested to create a document")
    await delete_previous_message(update)
    user_id = str(update.effective_user.id)
    query = update.callback_query

    if not has_premium_subscription(user_id):
        await query.message.reply_text(NO_PREMIUM_DOCUMENT_PROMPT, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Subscribe", callback_data="main_new_subscription")],
            [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")]
        ]))
        return

    await query.answer()
    context.user_data["awaiting_document"] = True
    await query.message.reply_text(DOCUMENT_PROMPT, reply_markup=get_back_to_menu_button())


async def handle_my_subscription(update: Update, context: CallbackContext):
    """
    Handles the user's subscription status request and provides relevant subscription details
    or options to subscribe.

    It validates if the user has an active subscription, retrieves subscription data, and
    displays appropriate information based on the subscription type. If no active subscription
    is found, the user is prompted with options to subscribe or return to the menu.

    :param update: The incoming update object that contains information about the user's interaction.
        Provides data necessary to access user details and handle interactions like callbacks.
    :type update: Update
    :param context: The callback context object that includes current context and additional
        data related to the bot's state.
    :type context: CallbackContext
    :return: Nothing is returned explicitly. Reacts to user interaction by replying with
        subscription details or prompts via messages.
    :rtype: None
    """
    from prompts import NO_SUBSCRIPTION_PROMPT
    logger.info(f"User {update.effective_user.id} requested subscription status")
    await delete_previous_message(update)
    query = update.callback_query
    await query.answer()

    user_id = str(update.effective_user.id)
    user_info = get_user_by_id(user_id)

    if not user_info or not user_info.get("subscription_active"):
        await query.message.reply_text(NO_SUBSCRIPTION_PROMPT, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Subscribe", callback_data="main_new_subscription")],
            [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")]
        ]))
        return

    sub_info = user_info.get("subscription_info", {})
    sub_type = sub_info.get("type", "Unknown")
    if sub_type == "basic":
        sub_type = "Consultation"
    elif sub_type == "premium":
        sub_type = "Basic"
    sub_start = format_date(sub_info.get("start", "-"))
    sub_end = format_date(sub_info.get("end", "-"))

    sub_text = (
        f"*Your Subscription:* {sub_type}\n"
        f"*Active From:* {sub_start}\n"
        f"*Until:* {sub_end}\n\n"
        "*Features Included in Your Plan:*\n"
    )

    if sub_type == "Consultation":
        sub_text += "• Answers to legal questions\n"
    elif sub_type == "Basic":
        sub_text += "• Answers, document creation and analysis (unlimited).\n"
    else:
        sub_text += "• Subscription details unavailable.\n"

    sub_text += "\n*The number of questions and documents is currently unlimited.*"

    keyboard = [
        [InlineKeyboardButton("🔁 Change Plan", callback_data="change_tariff")],
        [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")]
    ]

    await query.message.reply_text(sub_text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_new_subscription(update: Update, context: CallbackContext):
    """
    Handles the initiation of a new subscription flow for users. This function processes the user interaction
    with the bot to select a subscription tariff, providing options for different plans along with their pricing.
    It also manages the creation and presentation of a reply keyboard for user input.

    :param update: Represents incoming update information for the bot, such as messages or callback queries.
        The update type used in this function is `CallbackQuery`.
    :type update: telegram.Update
    :param context: Provides contextual information related to the current update, including data necessary
        for async operations and callback data.
    :type context: telegram.ext.CallbackContext

    :return: This function completes asynchronously and does not return any value.
    :rtype: None
    """
    from prompts import TARIFF_PROMPT
    logger.info(f"User {update.effective_user.id} started new subscription flow")
    await delete_previous_message(update)
    query = update.callback_query
    await query.answer()

    basic_price = TARIFF_PRICES.get("basic")
    premium_price = TARIFF_PRICES.get("premium")
    keyboard = [
        [InlineKeyboardButton(f"Consultation ({basic_price} ₽/month)", callback_data="tariff_basic")],
        [InlineKeyboardButton(f"Basic ({premium_price} ₽/month)", callback_data="tariff_premium")],
        [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")]
    ]

    await query.message.reply_text(TARIFF_PROMPT.format(basic_price=basic_price, premium_price=premium_price),
                                   parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_tariff_selection(update: Update, context: CallbackContext):
    """
    Handles the tariff selection process for a user. This function processes a user's callback
    query when they select a tariff plan, creates a payment for the selected tariff, and sends
    instructions for completing the payment. It also ensures that users do not attempt to renew
    an active subscription for the "basic" plan.

    :param update: Represents incoming update from Telegram, including details about
                   the user and their interaction (e.g., callback data).
    :type update: Update
    :param context: Provides contextual information about the ongoing Telegram bot interaction,
                    such as bot data and user data.
    :type context: CallbackContext
    :return: None
    :rtype: None
    """
    logger.info(f"User {update.effective_user.id} selected a tariff")
    await delete_previous_message(update)
    query = update.callback_query
    data = query.data
    user_id = str(update.effective_user.id)

    tariff_map = {
        "tariff_basic": "basic",
        "tariff_premium": "premium",
    }
    tariff_names = {
        "basic": "Consultation",
        "premium": "Basic",
    }

    chosen_tariff_code = tariff_map.get(data)
    chosen_tariff_name = tariff_names.get(chosen_tariff_code, "Unknown")

    if chosen_tariff_code == "basic" and has_basic_subscription(user_id):
        await query.answer()
        await query.message.reply_text(
            "You already have an active *Consultation* subscription. No need to renew.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_back_to_menu_button()
        )
        return

    user_id_int = update.effective_user.id

    try:
        payment_result = create_payment(user_id_int, chosen_tariff_code)
        logger.info(f"Payment creation result for user {user_id}: {bool(payment_result)}")
    except Exception as e:
        logger.error(f"Error creating payment for user {user_id}: {e}")
        payment_result = None

    if payment_result:
        success = add_payment_to_monitor(
            payment_result["payment_id"],
            user_id,
            chosen_tariff_code
        )

        if success:
            logger.info(f"Payment {payment_result['payment_id']} added to monitoring for user {user_id}")
        else:
            logger.error(f"Failed to add payment {payment_result['payment_id']} to monitoring")

        keyboard = [
            [InlineKeyboardButton("Proceed to Payment", url=payment_result["confirmation_url"])],
            [InlineKeyboardButton("Check Payment", callback_data="check_payment")],
            [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")]
        ]
        await query.answer()
        await query.message.reply_text(
            f"To subscribe to *{chosen_tariff_name}*, follow the link and complete the payment.\n\n"
            "Once the payment is successful, your subscription will be activated automatically, and you will receive a notification.\n\n"
            "💡 If the subscription is not activated after payment, press the \"Check Payment\" button.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        logger.error("Payment creation failed")
        await query.answer("An error occurred while creating the payment")
        await query.message.reply_text(PAYMENT_FAILURE_MESSAGE, reply_markup=get_back_to_menu_button())


async def handle_change_tariff(update: Update, context: CallbackContext):
    """
    Handles the change tariff interaction for the user. This function responds to a user's request to
    view tariff options by generating a message with available tariff plans and their respective prices in a
    formatted keyboard layout.

    :param update: An instance of telegram's Update class that contains information about the current update.
    :param context: An instance of telegram.ext.CallbackContext providing contextual information for the
        callback, including data and helper methods.
    :return: None
    """
    from prompts import TARIFF_PROMPT
    logger.info(f"User {update.effective_user.id} opened change tariff options")
    await delete_previous_message(update)
    query = update.callback_query

    basic_price = TARIFF_PRICES.get("basic", "199")
    premium_price = TARIFF_PRICES.get("premium", "699")
    keyboard = [
        [InlineKeyboardButton(f"Consultation ({basic_price} ₽/month)", callback_data="tariff_basic")],
        [InlineKeyboardButton(f"Basic ({premium_price} ₽/month)", callback_data="tariff_premium")],
        [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")],
    ]

    await query.message.reply_text(TARIFF_PROMPT.format(basic_price=basic_price, premium_price=premium_price),
                                   parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(keyboard))


async def handle_check_payment(update: Update, context: CallbackContext):
    """
    Handles the check payment callback triggered by the user. This function performs a manual
    verification of user payments by interacting with the payment service. It informs the user
    about the current state of their payments, specifically highlighting any pending payments or
    providing assurance if no payments are pending.

    This function operates asynchronously, managing the interaction flow by deleting previous
    messages, managing callback queries, and sending appropriate responses depending on the
    payment service's response.

    :param update: The Update object that contains information about the incoming update, including
                   the callback query and user details.
    :type update: telegram.Update
    :param context: The CallbackContext object that provides the context for the callback function,
                    including access to bot data, chat data, user data, and other utilities.
    :type context: telegram.ext.CallbackContext
    :return: None. The function performs communication via Telegram API and does not return any
             explicit value.

    """
    logger.info(f"User {update.effective_user.id} requested manual payment check")
    await delete_previous_message(update)
    query = update.callback_query
    user_id = str(update.effective_user.id)

    try:
        payment_info = await check_user_payments(user_id)

        if "error" in payment_info:
            await query.answer()
            await query.message.reply_text(
                "❌ The payment verification service is temporarily unavailable. Please try again later.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_back_to_menu_button()
            )
            return

        pending_count = payment_info["total_pending"]

        if pending_count > 0:
            await query.answer()
            await query.message.reply_text(
                f"🔍 Checking your payments...\n\n"
                f"Pending payments found: {pending_count}\n\n"
                "If the payment was successful, your subscription will be activated within a minute.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_back_to_menu_button()
            )
            logger.info(f"Found {pending_count} pending payments for user {user_id}")

            for payment in payment_info["pending_payments"]:
                logger.info(f"User {user_id} pending payment: {payment['payment_id'][:8]}... "
                            f"(tariff: {payment['tariff_type']}, age: {payment['age_minutes']:.1f}m)")


        else:
            await query.answer()
            await query.message.reply_text(
                "ℹ️ No pending payments found.\n\n"
                "If you paid recently, please try again in a few minutes or contact support.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_back_to_menu_button()
            )
            logger.info(f"No pending payments found for user {user_id}")

    except Exception as e:
        logger.error(f"Error during manual payment check for user {user_id}: {e}")
        await query.answer()
        await query.message.reply_text(
            "❌ An error occurred during payment check. Please try again later.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_back_to_menu_button()
        )


async def handle_payment_success(update: Update, context: CallbackContext, payment_data=None):
    """
    Handles a successful payment event for a user, updates their subscription,
    and sends a confirmation message. This function processes both direct
    payment data and pending payment data stored in the user context. It also
    handles cases where payment data is missing or invalid and informs the
    user about the status accordingly. Finally, confirmed payment updates
    relevant user data and provides further interaction options to the user
    via a main menu.

    :param update: The incoming Telegram update event that triggered this handler.
    :type update: telegram.Update
    :param context: Callback context providing access to user-related data
        and bot infrastructure.
    :type context: telegram.ext.CallbackContext
    :param payment_data: Optional payment details provided directly to the
        function. If not provided, attempts to retrieve the details from the
        user context data.
    :type payment_data: dict, optional
    :return: None
    """
    from prompts import SUBSCRIPTION_SUCCESS_MESSAGE
    logger.info(f"Handling payment success for user {update.effective_user.id}")
    await delete_previous_message(update)

    if not payment_data:
        payment_id = context.user_data.get("pending_payment_id")
        if not payment_id:
            await update.message.reply_text(
                "Failed to retrieve payment information. Please contact support.",
                reply_markup=get_back_to_menu_button()
            )
            return

        payment_data = check_payment_status(payment_id)
        if not payment_data or payment_data["status"] != "succeeded":
            await update.message.reply_text(
                "Payment information not found or the payment is not completed. Please check the payment status.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Check Payment Status", callback_data="check_payment")],
                    [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")]
                ])
            )
            return

    user_id = str(update.effective_user.id)
    tariff_names = {"basic": "Consultation", "premium": "Basic"}
    tariff_type = payment_data.get("tariff_type")
    tariff_name = tariff_names.get(tariff_type, "Unknown")
    subscription_start = payment_data.get("subscription_start")
    subscription_end = payment_data.get("subscription_end")

    update_subscription(user_id, sub_type=tariff_type, start=subscription_start)
    update_payment_method(user_id, payment_data.get("payment_method_id"))

    context.user_data.pop("pending_payment_id", None)
    context.user_data.pop("pending_subscription", None)

    message_text = SUBSCRIPTION_SUCCESS_MESSAGE.format(tariff_name=tariff_name, subscription_start=subscription_start,
                                                       subscription_end=subscription_end)

    if hasattr(update, "message") and update.message:
        await update.message.reply_text(message_text, parse_mode=ParseMode.MARKDOWN)
        await show_main_menu(update, context)
    elif hasattr(update, "callback_query") and update.callback_query:
        await update.callback_query.message.reply_text(message_text, parse_mode=ParseMode.MARKDOWN)
        await show_main_menu(update, context)


async def process_yookassa_webhook(update: Update, context: CallbackContext) -> str:
    """
    Handles a YooKassa webhook for processing payment events and sends appropriate
    responses to the user based on the payment status.

    This function receives updates from the webhook, checks the type of event
    received, validates the payment status, and sends confirmation messages
    to the user if the payment was successful. In case of an error, an appropriate
    message is logged, and an error response is returned.

    :param update:
        The update object representing incoming webhook data from the YooKassa server.
    :param context:
        The callback context object allowing interaction with the bot and its resources.
    :return:
        A string response indicating the outcome of the webhook processing,
        either "OK" for success or "Error" in case of failure.
    """
    from prompts import PAYMENT_SUCCESS_MESSAGE
    logger.info("Received YooKassa webhook")
    webhook_data = update.message.web_app_data.data

    try:
        event_type = webhook_data.get("event")
        if event_type == "payment.succeeded":
            payment_id = webhook_data.get("object", {}).get("id")
            if payment_id:
                payment_data = check_payment_status(payment_id)
                if payment_data and payment_data["status"] == "succeeded":
                    user_id = payment_data.get("user_id")
                    if user_id:
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=PAYMENT_SUCCESS_MESSAGE,
                            reply_markup=get_back_to_menu_button()
                        )
        return "OK"
    except Exception as e:
        logger.error(f"Error processing YooKassa webhook: {e}", exc_info=True)
        return "Error"


async def handle_history(update: Update, context: CallbackContext):
    """
    Handles the request to display the user's interaction history including previously
    saved sessions, and provides options to navigate back to the menu or delete history.

    :param update: The update object containing information about the incoming update.
    :type update: Update
    :param context: The context object containing data related to the current callback
        and allowing interaction with the bot's context.
    :type context: CallbackContext
    :return: This is an asynchronous function and does not return anything directly.
    :rtype: None
    """
    logger.info(f"User {update.effective_user.id} requested history")
    await delete_previous_message(update)
    user_id = str(update.effective_user.id)

    sessions = get_user_sessions_summary(user_id)
    if not sessions:
        await update.callback_query.message.reply_text("❗ You don't have any saved conversations yet.",
                                                       reply_markup=get_back_to_menu_button())
        await update.callback_query.answer()
        return

    keyboard = [
        [InlineKeyboardButton(f"{format_timestamp(session['timestamp'])} — {session['preview']}...",
                              callback_data=f"history_open_{session['index']}")]
        for session in sessions
    ]
    keyboard.append([InlineKeyboardButton("🗑 Delete History", callback_data="history_delete_confirm")])
    keyboard.append([InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.message.reply_text("📜 History of your previous requests:", reply_markup=reply_markup)
    await update.callback_query.answer()


async def handle_history_callbacks(update: Update, context: CallbackContext):
    """
    Handles user interactions with historical conversation data via Telegram bot callback queries.

    This asynchronous function processes callback queries related to user conversation history,
    such as viewing specific historical sessions, confirming or canceling history deletions, and
    managing individual or all conversation histories.

    :param update: An instance of the telegram.ext.Update class. Holds the update event data received
        from the Telegram bot, including user information, callback query data, and any associated messages.
    :param context: An instance of the telegram.ext.CallbackContext class. Provides contextual
        information about the interaction and allows shared data between different handlers.

    :return: None. The function executes asynchronously and interacts with the Telegram bot API
        to send relevant replies or perform operations based on the callback query data.
    """
    user = update.effective_user
    query = update.callback_query
    data = query.data
    user_id = str(query.from_user.id)
    await delete_previous_message(update)

    if data.startswith("history_open_"):
        logger.info(f"User {user_id} opened a session from history")
        try:
            index = int(data.replace("history_open_", ""))
            session = move_session_to_end(user_id, index)
            if not session:
                await query.message.reply_text("❗ Failed to find the conversation.",
                                               reply_markup=get_back_to_menu_button())
                return
            context.user_data["current_request"] = True

            messages = session.get("dialog", [])
            if not messages:
                await query.message.reply_text("📂 The conversation is empty or corrupted.",
                                               reply_markup=get_back_to_menu_button())
                return

            history_text = "\n\n".join([f"{'👤' if m['role'] == 'user' else '🤖'} {m['message']}" for m in messages])
            await query.message.reply_text(f"📂Opened conversation:\n\n{history_text[:4000]}",
                                           reply_markup=InlineKeyboardMarkup([
                                               [InlineKeyboardButton("🔁 Continue", callback_data="continue_dialog")],
                                               [InlineKeyboardButton("🗑 Delete Conversation",
                                                                     callback_data="history_delete_single_dialog")],
                                               [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")]
                                           ]))
        except Exception as e:
            logger.error(f"Error in history_open: {e}", exc_info=True)
            await query.message.reply_text("❗ An error occurred while opening the conversation.")


    elif data == "history_delete_confirm":
        logger.info(f"User {user_id} initiated history deletion confirmation")
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes, delete", callback_data="history_delete")],
            [InlineKeyboardButton("❌ Cancel", callback_data="history_cancel")]
        ])

        await query.message.reply_text("Are you sure you want to delete all history?", reply_markup=keyboard)


    elif data == "history_delete":
        try:
            logger.info(f"User {user_id} confirmed history deletion")
            delete_user_history(user_id)
            await query.message.reply_text("🗑 Conversation history deleted.", reply_markup=get_back_to_menu_button())
        except Exception as e:
            logger.error(f"Error in history_delete: {e}", exc_info=True)
            await query.message.reply_text("❗ An error occurred while deleting history.",
                                           reply_markup=get_back_to_menu_button())

    elif data == "history_cancel":
        logger.info(f"User {user_id} canceled history deletion")
        await query.message.reply_text("❌ Deletion canceled.", reply_markup=get_back_to_menu_button())

    elif data == "history_delete_single_dialog":
        try:
            logger.info(f"User {user_id} is deleting single dialog")
            delete_last_session(user_id)
            await query.message.reply_text("🗑 Conversation deleted.", reply_markup=get_back_to_menu_button())
        except Exception as e:
            logger.error(f"Error in delete_single_dialog: {e}", exc_info=True)
            await query.message.reply_text("❗ An error occurred while deleting the conversation.",
                                           reply_markup=get_back_to_menu_button())


async def handle_rate_document(update: Update, context: CallbackContext):
    """
    Handles the request from a user to rate a document. This is an asynchronous
    callback function that responds to a user's interaction, deletes previous
    messages related to the interaction, and presents a new prompt with an inline
    keyboard for rating a document or returning to the menu.

    :param update: Contains all the information and data related to the incoming
        update, including user details, the interaction context, and associated
        callback query.
    :type update: Update

    :param context: Provides the handler's context, such as data persistence and
        user-specific data used to track ongoing interactions and states.
    :type context: CallbackContext

    :return: None
    """
    from prompts import RATE_DOCUMENT_PROMPT
    logger.info(f"User {update.effective_user.id} requested to rate a document")
    await delete_previous_message(update)
    query = update.callback_query

    await query.answer()
    context.user_data["awaiting_rating"] = True
    await query.message.reply_text(RATE_DOCUMENT_PROMPT,
                                   reply_markup=InlineKeyboardMarkup(
                                       [[InlineKeyboardButton("↩️ Back to Menu"
                                                              , callback_data="back_to_menu")]]),
                                   parse_mode="Markdown"
                                   )


async def handle_create_document_from_response(update: Update, context: CallbackContext):
    """
    Handles the creation of a document based on the response generated by the model.

    This asynchronous function is triggered when a user requests to create a document
    from the model's response. It checks the user's subscription status and prompts
    them with appropriate actions. If the user has a valid premium subscription,
    further steps are initiated to facilitate the creation of the document.

    :param update: An object containing information about the received update.
    :param context: An object containing context relevant to the current handler.
    :return: None
    """
    from prompts import DOCUMENT_PROMPT
    logger.info(f"User {update.effective_user.id} requested to create document from model response")
    await delete_previous_message(update)
    query = update.callback_query
    user_id = str(update.effective_user.id)

    if not has_premium_subscription(user_id):
        from prompts import NO_PREMIUM_DOCUMENT_PROMPT
        await query.message.reply_text(NO_PREMIUM_DOCUMENT_PROMPT, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Subscribe", callback_data="main_new_subscription")],
            [InlineKeyboardButton("↩️ Back to Menu", callback_data="back_to_menu")]
        ]))
        return

    await query.answer()
    context.user_data["awaiting_document"] = True

    model_response = context.user_data.get('last_model_response', '')
    context.user_data['document_context'] = model_response

    await query.message.reply_text(
        "📄 You can create a document based on the generated response. "
        "Please send the document text or specify what type of document you need.",
        reply_markup=get_back_to_menu_button()
    )
