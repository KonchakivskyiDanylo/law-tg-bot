"""
Handles legal query processing via OpenAI's API: combined evaluation and decomposition for faster responses.
"""
import logging
import json
import re
from openai import AsyncOpenAI
from config.config import OPENAI_API_KEY
from typing import Optional
from prompts import LEGAL_ADVISOR_PROMPT, LEGAL_RESEARCH_PROMPT, RESPONSE_SYNTHESIS_PROMPT, \
    DEFINITION_PROMPT, COMBINED_EVALUATION_DECOMPOSITION_PROMPT

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=OPENAI_API_KEY)


async def get_legal_term_definition(term: str, language: str = "english") -> str:
    """
    Retrieve the definition of a legal term in the specified language using an
    AI-driven client response. By default, the language is set to Russian ('ru').
    The method utilizes tools such as web search to fetch an appropriate definition.

    :param term: The legal term to retrieve the definition for.
    :type term: str
    :param language: The language in which the definition should be provided
        (default is "ru").
    :type language: str, optional
    :return: The retrieved definition of the specified legal term. If no definition
        is found, returns a failure message. If an error occurs, returns an error
        message.
    :rtype: str
    """
    logger.info(f"ENTER get_legal_term_definition(term={term}, language={language})")
    language = "english"
    try:
        response = await client.responses.create(
            model="gpt-4.1",
            input=[{"role": "user", "content": DEFINITION_PROMPT.format(language=language, term=term)}],
            tools=[{"type": "web_search"}],
        )

        result_text = ""
        if response.output:
            for item in response.output:
                if item.type == "message" and item.content:
                    for content in item.content:
                        if content.type == "output_text":
                            result_text = content.text
                            break

        if not result_text:
            return f"Failed to find a definition for the term '{term}'."
        return result_text

    except Exception as e:
        logger.error(f"Error in get_legal_term_definition: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return f"An error occurred while searching for the definition of the term '{term}'."


async def handle_legal_query(query: str, conversation_history: list = None,
                             previous_response_id: Optional[str] = None) -> dict:
    """
    Handles a legal-related query by appending it to the conversation history and formatting
    the messages appropriately for processing by the system. It ensures that a system-level
    prompt is included in the conversation prior to processing.

    :param query: The string representing the user's legal-related query.
    :type query: str
    :param conversation_history: A list containing the conversation history messages, where
        each message is represented as a dictionary with "role" and "content" keys. Defaults
        to an empty list if not provided.
    :type conversation_history: list, optional
    :param previous_response_id: The string identifier of the response generated in the
        previous interaction, if any. Defaults to None.
    :type previous_response_id: str, optional
    :return: A dictionary containing the processed legal question result.
    :rtype: dict
    """
    logger.info(
        f"ENTER handle_legal_query(query={query[:50]}..., conversation_history=[...], previous_response_id={previous_response_id})")

    if conversation_history is None:
        conversation_history = []

    messages = conversation_history.copy()
    messages.append({"role": "user", "content": query})

    messages.insert(0, {"role": "system", "content": LEGAL_ADVISOR_PROMPT})

    return await process_legal_question(messages, previous_response_id)


async def process_legal_question(messages: list[dict], previous_response_id: Optional[str] = None) -> dict:
    """
    Processes a legal question and generates a response based on analysis, research, and synthesis.

    The function evaluates a given legal question, determines the adequacy of provided details,
    and returns a suitable response. If additional information is required, the function
    requests clarifications. For well-defined questions, the function conducts further
    research and provides synthesized legal insights along with a disclaimer.

    :param messages: A list of dictionaries representing the conversation history, where each dictionary
        contains details such as the user message and metadata relevant to the legal question processing.
    :param previous_response_id: An optional string representing the ID of the previous response in the
        session. Used for continuity and context preservation in multi-turn conversations.
    :return: A dictionary containing the generated response text, response ID (for tracking),
        and a boolean indicating whether the process is complete or if further clarification is needed.
    """
    logger.info(f"ENTER process_legal_question(messages=[...], previous_response_id={previous_response_id})")

    evaluation_decomposition = await evaluate_and_decompose_question(messages, previous_response_id)

    if not evaluation_decomposition:
        error_msg = "Sorry, an error occurred while analyzing your legal question. Please try rephrasing your question or consult a lawyer."
        return {"response_text": error_msg, "response_id": None, "is_complete": True}

    if evaluation_decomposition.get("score", 0) < 3:
        clarifying_questions = evaluation_decomposition.get("clarifying_questions",
                                                            ["Please provide more information about your situation."])
        explanation = evaluation_decomposition.get("explanation", "Insufficient information for legal analysis.")

        response_text = "To provide accurate legal information, I need additional details:\n\n"
        for i, question in enumerate(clarifying_questions, 1):
            response_text += f"{i}. {question}\n"

        response_text += f"\n{explanation}"

        return {
            "response_text": response_text,
            "response_id": evaluation_decomposition.get("response_id"),
            "is_complete": False
        }

    research = await perform_legal_research(evaluation_decomposition, messages,
                                            evaluation_decomposition.get("response_id"))
    if not research.get("research_result"):
        error_msg = "Sorry, an error occurred while searching for legal information regarding your question. I recommend consulting a professional lawyer for accurate advice."
        return {"response_text": error_msg, "response_id": None, "is_complete": True}

    final_response = await synthesize_legal_response(research.get("research_result"), messages,
                                                     research.get("response_id"))
    if not final_response.get("response_text"):
        error_msg = "Sorry, an error occurred while generating the response. I recommend consulting a professional lawyer for advice on your question."
        return {"response_text": error_msg, "response_id": None, "is_complete": True}

    response_text = final_response.get("response_text")
    if "for informational purposes" not in response_text.lower() and "does not replace consultation" not in response_text.lower():
        response_text += "\n\n**Legal disclaimer**: This information is for reference only and does not replace consultation with a qualified lawyer. For resolution of specific legal issues, it is recommended to consult a specialist."

    return {
        "response_text": response_text,
        "response_id": final_response.get("response_id"),
        "is_complete": True
    }


async def evaluate_and_decompose_question(messages: list[dict], previous_response_id: Optional[str] = None) -> dict:
    """
    Evaluates and decomposes a user's question by interacting with an external AI model.
    The function processes the user's input messages, sends them to the client for
    evaluation, and parses the response. Based on the response, a score, potential
    clarifying questions, and an explanation are returned. It handles specific
    formatting and potential JSON parsing issues in the response.

    :param messages: List of dictionaries representing user-provided messages to be
        evaluated and decomposed.
    :type messages: list[dict]
    :param previous_response_id: Optional identifier for an earlier processed response.
        Used for reference in case of incremental processing.
    :type previous_response_id: Optional[str]
    :return: Dictionary containing the score of the evaluated question, clarifying
        questions (if applicable), an explanation, and the response ID. The score
        indicates the completeness of the question (higher scores represent better
        completeness), while clarifying questions are provided if additional input is
        needed.
    :rtype: dict
    """
    logger.info(f"ENTER evaluate_and_decompose_question(messages=[...], previous_response_id={previous_response_id})")

    try:
        response = await client.responses.create(
            model="gpt-4.1",
            instructions=COMBINED_EVALUATION_DECOMPOSITION_PROMPT,
            input=messages,
            previous_response_id=previous_response_id
        )

        result_text = ""
        if response.output:
            for item in response.output:
                if item.type == "message" and item.content:
                    for content in item.content:
                        if content.type == "output_text":
                            result_text = content.text
                            break

        if not result_text:
            logger.error("No text content found in the response")
            logger.info("EXIT evaluate_and_decompose_question -> default (score=1, empty questions)")
            return {"score": 1, "clarifying_questions": [
                "Please clarify your legal question by providing more details about the situation."],
                    "explanation": "No response from model.", "response_id": None}

        cleaned = result_text.strip()
        if '```json' in cleaned:
            match = re.search(r'```json\s*(.*?)\s*```', cleaned, re.DOTALL)
            if match:
                cleaned = match.group(1)
        elif '```' in cleaned:
            match = re.search(r'```\s*(.*?)\s*```', cleaned, re.DOTALL)
            if match:
                cleaned = match.group(1)

        try:
            result = json.loads(cleaned)
            result['response_id'] = response.id
            score = result.get('score', 0)
            if score >= 3:
                logger.info(f"Question is complete (score={score}), decomposition: {result.get('decomposition', {})}")
            else:
                logger.info(
                    f"Question needs clarification (score={score}), questions: {result.get('clarifying_questions', [])}")

            logger.info(f"EXIT evaluate_and_decompose_question -> {result}")
            return result
        except Exception as je:
            logger.error(f"JSON decode error: {je}")
            logger.error(f"Raw response: {cleaned}")
            logger.info("EXIT evaluate_and_decompose_question -> fallback (score=1, empty questions)")
            return {"score": 1, "clarifying_questions": ["Please clarify your question."],
                    "explanation": "Error with JSON.", "response_id": response.id}

    except Exception as e:
        logger.error(f"Error in evaluate_and_decompose_question: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        logger.info("EXIT evaluate_and_decompose_question -> fallback (score=1, empty questions)")
        return {"score": 1, "clarifying_questions": ["Please clarify your question"],
                "explanation": "Error.", "response_id": None}


async def perform_legal_research(evaluation_decomposition: dict, messages: list[dict],
                                 previous_response_id: Optional[str] = None) -> dict:
    """
    Performs legal research by utilizing a given decomposition of a legal question,
    interacting with an external AI model, and returning the extracted research result.
    The method augments an existing message list with additional system instructions
    based on the provided decomposition, sends it to the AI, retrieves the response,
    and processes it to extract the relevant text.

    :param evaluation_decomposition: A dictionary containing information about the decomposition
        of a legal problem.
    :param messages: A list of dictionaries representing the ongoing conversation messages,
        structured with roles and content.
    :param previous_response_id: An optional string identifier for a previous response, allowing
        continuity in AI interaction.
    :return: A dictionary containing the research result and the associated response ID.
    """
    logger.info(
        f"ENTER perform_legal_research(evaluation_decomposition={evaluation_decomposition}, messages=[...], previous_response_id={previous_response_id})")

    decomposition = evaluation_decomposition.get('decomposition', {})

    enhanced_messages = messages.copy()
    enhanced_messages.append({
        "role": "system",
        "content": f"Legal question decomposition: {json.dumps(decomposition, ensure_ascii=False)}"
    })

    try:
        response = await client.responses.create(
            model="gpt-4.1",
            instructions=LEGAL_RESEARCH_PROMPT,
            input=enhanced_messages,
            max_output_tokens=1000,
            tools=[{"type": "web_search"}],
            tool_choice="required",
            previous_response_id=previous_response_id
        )

        result_text = ""
        if response.output:
            for item in response.output:
                if item.type == "message" and item.content:
                    for content in item.content:
                        if content.type == "output_text":
                            result_text = content.text
                            break

        if not result_text:
            logger.error("No text content found in the legal research response")
            return {"research_result": None, "response_id": None}

        truncated_result = result_text[:200] + "..." if len(result_text) > 200 else result_text
        logger.info(f"Legal research response: {truncated_result}")

        return {
            "research_result": result_text,
            "response_id": response.id
        }

    except Exception as e:
        logger.error(f"Error in perform_legal_research: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {"research_result": None, "response_id": None}


async def synthesize_legal_response(research_result: str, messages: list[dict],
                                    previous_response_id: Optional[str] = None) -> dict:
    """
    Synthesize a legal response based on research results and a sequence of prior messages.
    This function interacts with an external AI client to produce a synthesized textual
    response. It includes appending research data to the messages, handling AI-generated
    responses, and logging results or errors during the process.

    :param research_result: Legal research data used as contextual input for generating the
                            response.
    :type research_result: str
    :param messages: A sequence of prior interaction messages to include in the request.
                     Each message is represented as a dictionary with role and content.
    :type messages: list[dict]
    :param previous_response_id: Optional identifier for the previous response to maintain
                                  response continuity during iterative synthesis.
    :type previous_response_id: Optional[str]
    :return: A dictionary with the synthesized response text and the response identifier if
             successfully created, or default values when operation fails.
    :rtype: dict
    """
    logger.info(
        f"ENTER synthesize_legal_response(research_result=..., messages=[...], previous_response_id={previous_response_id})")

    enhanced_messages = messages.copy()
    enhanced_messages.append({
        "role": "system",
        "content": f"Legal research results: {research_result}"
    })

    try:
        response = await client.responses.create(
            model="o4-mini",
            instructions=RESPONSE_SYNTHESIS_PROMPT,
            input=enhanced_messages,
            previous_response_id=previous_response_id
        )

        result_text = ""
        if response.output:
            for item in response.output:
                if item.type == "message" and item.content:
                    for content in item.content:
                        if content.type == "output_text":
                            result_text = content.text
                            break

        if not result_text:
            logger.error("No text content found in the response synthesis")
            return {"response_text": None, "response_id": None}

        truncated_result = result_text[:200] + "..." if len(result_text) > 200 else result_text
        logger.info(f"Synthesized legal response: {truncated_result}")

        return {
            "response_text": result_text,
            "response_id": response.id
        }

    except Exception as e:
        logger.error(f"Error in synthesize_legal_response: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {"response_text": None, "response_id": None}
