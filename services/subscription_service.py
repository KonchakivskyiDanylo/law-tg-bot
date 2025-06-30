import logging
from datetime import datetime, timedelta
from telegram.ext import Application
from typing import Optional

from repositories.user_repository import (
    get_all_active_users,
    update_user,
    clear_user_history,
    set_user_sessions,
    get_user_by_id,
    push_to_user_array
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def deactivate_subscription(user_id: str, remove_payment_method: bool = True) -> None:
    """
    Deactivates a user's subscription by updating their subscription status and
    optionally removing their stored payment method.

    :param user_id: A string representing the unique identifier of the user whose
        subscription will be deactivated.
    :param remove_payment_method: A boolean indicating whether the user's stored
        payment method should be removed. Defaults to True.
    :return: None
    """
    update_fields = {
        "subscription_active": False,
        "subscription_info": {
            "type": "",
            "start": "",
            "end": ""
        }
    }
    if remove_payment_method:
        update_fields["payment_method_id"] = None

    logger.info(f"Deactivating subscription for user {user_id}")
    update_user(user_id, update_fields)


def update_subscription(user_id: str, sub_type: str, start: str = None) -> None:
    """
    Updates the subscription details for a user in the database. If a starting date is not
    provided, it attempts to retrieve an existing starting date from the user's subscription
    data. If no valid existing start date is found, the current date is used as the default
    starting date. The subscription is updated with the new type, start date, and an
    automatically calculated end date of 30 days from the current date.

    :param user_id: Unique identifier for the user whose subscription is to be updated.
    :type user_id: str
    :param sub_type: Type of subscription to be assigned to the user.
    :type sub_type: str
    :param start: Optional starting date for the subscription in ISO 8601 format. If not provided,
        retrieves it from existing user data or uses the current date as default.
    :type start: Optional[str]
    :return: This function does not return any value.
    :rtype: None
    """
    user = get_user_by_id(user_id)

    if not user:
        logger.warning(f"User {user_id} not found when updating subscription.")
        return

    today = datetime.utcnow().date()
    subscription_info = user.get("subscription_info", {})
    existing_start_str = subscription_info.get("start")
    existing_end_str = subscription_info.get("end")

    try:
        existing_start = datetime.fromisoformat(existing_start_str).date() if existing_start_str else None
    except ValueError:
        existing_start = None

    try:
        existing_end = datetime.fromisoformat(existing_end_str).date() if existing_end_str else None
    except ValueError:
        existing_end = None

    try:
        new_start_date = datetime.strptime(start, "%d.%m.%Y").date() if start else today
    except Exception:
        new_start_date = today

    if existing_end and new_start_date <= existing_end and existing_start:
        start_date = existing_start
    else:
        start_date = new_start_date

    end_date = new_start_date + timedelta(days=30)

    subscription_data = {
        "type": sub_type,
        "start": start_date.isoformat(),
        "end": end_date.isoformat()
    }

    logger.info(f"Updating subscription for user {user_id} to type {sub_type}")
    update_user(user_id, {
        "subscription_active": True,
        "subscription_info": subscription_data
    })


def delete_last_session(user_id: str) -> None:
    """
    Delete the last session for a specified user.

    This function retrieves a user by their unique identifier (`user_id`) and attempts
    to remove the last session from their list of previous requests. If the user does not
    exist or has no previous sessions, appropriate warnings are logged. Upon successful
    deletion, relevant information about the removed session is logged.

    :param user_id: The unique identifier of the user whose last session
        should be deleted.
    :type user_id: str
    :return: None
    """
    user = get_user_by_id(user_id)
    if not user:
        logger.warning(f"User {user_id} not found for last session deletion")
        return

    sessions = user.get("previous_requests", [])
    if not sessions:
        logger.warning(f"No sessions to delete for user {user_id}")
        return

    removed = sessions.pop()  # delete the last one
    logger.info(f"Deleted last session for user {user_id}: {removed.get('initial_question', '...')}")
    set_user_sessions(user_id, sessions)


def update_payment_method(user_id: str, payment_method_id: str) -> None:
    """
    Update the payment method for a specific user in the database.

    This function updates the payment method of a user using their unique user ID
    and the payment method ID corresponding to the new payment method. It interacts
    with the database to set the `payment_method_id` for the user specified.

    :param user_id: The unique identifier of the user whose payment method is to be updated.
    :param payment_method_id: The unique identifier of the payment method to be assigned to the user.
    :return: No return value. The function operates with a side effect of updating the database.
    """
    logger.info(f"Updating payment method for user {user_id}")
    update_user(user_id, {"payment_method_id": payment_method_id})


def has_premium_subscription(user_id: str) -> bool:
    """
    Determines if a user has an active premium subscription.

    This function checks whether a user with the specified user ID has
    an active premium subscription. It retrieves the user's details using
    `get_user_by_id` and examines the subscription information and status.

    :param user_id: The unique identifier of the user.
    :type user_id: str
    :return: True if the user has an active premium subscription, False otherwise.
    :rtype: bool
    """
    user = get_user_by_id(user_id)
    return user.get("subscription_info", {}).get("type") == "premium" if user and user.get(
        "subscription_active") else False


def has_basic_subscription(user_id: str) -> bool:
    """
    Checks if a user has an active basic subscription. The function fetches the user data
    based on the provided user ID and evaluates if the user has an active subscription of type "basic".
    It ensures that the user exists and their subscription status is active before performing this check.

    :param user_id: The unique identifier of a user.
    :type user_id: str
    :return: True if the user has an active basic subscription, otherwise False.
    :rtype: bool
    """
    user = get_user_by_id(user_id)
    return user.get("subscription_info", {}).get("type") == "basic" if user and user.get(
        "subscription_active") else False


def has_few_chats_last_30_days(user_id: str) -> bool:
    """
    Determines if a user has had fewer than two chats in the last 30 days.

    This function fetches the user by their ID, retrieves their previous requests, and
    counts the number of valid requests made within the last 30 days. If the number of
    requests is fewer than two, the function will return True; otherwise, it returns False.
    If the user does not exist, the function assumes the user has had fewer than two chats.

    :param user_id: The unique identifier of the user.
    :type user_id: str
    :return: True if the user has had fewer than two chats in the last 30 days, False otherwise.
    :rtype: bool
    """
    user = get_user_by_id(user_id)
    if not user:
        return True

    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    requests = user.get("previous_requests", [])

    count = sum(
        1 for r in requests
        if "timestamp" in r and datetime.fromisoformat(r["timestamp"]) >= thirty_days_ago
    )

    return count < 2


async def check_subscriptions(application: Application) -> None:
    """
    Checks user subscriptions and sends notifications if their end dates are near.
    This function queries the `collection` to identify users with active
    subscriptions. If a subscription is set to expire today, it deactivates the
    subscription and notifies the user. If a subscription is set to expire in
    three days, it sends a reminder notification to the user.

    :param application: The application context, which includes the bot
        instance used to send messages.
    :return: None

    :raises Exception: May raise exceptions during bot message sending
        operations or subscription deactivation.
    """
    today = datetime.utcnow().date()
    three_days_later = today + timedelta(days=3)

    users = get_all_active_users()
    logger.info(f"Checking subscriptions for {users.count()} active users")

    for user in users:
        user_id = str(user["_id"])
        chat_id = int(user_id)

        sub_info = user.get("subscription_info", {})
        end_date_str = sub_info.get("end")
        if not end_date_str:
            continue

        try:
            end_date = datetime.fromisoformat(end_date_str).date()
        except ValueError:
            logger.warning(f"Invalid end_date for user {user_id}: {end_date_str}")
            continue

        if end_date == three_days_later:
            try:
                from prompts import SUBSCRIPTION_WARNING_PROMPT
                logger.info(f"Sending 3-day warning to user {user_id}")
                await application.bot.send_message(
                    chat_id=chat_id,
                    text=SUBSCRIPTION_WARNING_PROMPT
                )
            except Exception as e:
                logger.error(f"Failed to send 3-day warning to {user_id}: {e}", exc_info=True)

        elif end_date == today:
            try:
                from prompts import SUBSCRIPTION_EXPIRED_PROMPT
                logger.info(f"Deactivating expired subscription for user {user_id}")
                deactivate_subscription(user_id)
                await application.bot.send_message(
                    chat_id=chat_id,
                    text=SUBSCRIPTION_EXPIRED_PROMPT
                )
            except Exception as e:
                logger.error(f"Failed to handle expiration for user {user_id}: {e}", exc_info=True)


def start_new_request_session(user_id: str, question: str, session_type: str) -> None:
    """
    Starts a new request session for a user, initializing session data with the initial
    question, session type, and timestamp. This function logs the initiation of the session
    and updates the user's record with the newly created session.

    :param user_id: The unique identifier of the user.
    :type user_id: str
    :param question: The initial question posed by the user to start the session.
    :type question: str
    :param session_type: The type or category of the session being created.
    :type session_type: str
    :return: None
    """
    session = {
        "initial_question": question,
        "type": session_type,
        "dialog": [
            {"role": "user", "message": question}
        ],
        "timestamp": datetime.utcnow().isoformat()
    }
    logger.info(f"Starting new session for user {user_id}")
    push_to_user_array(user_id, "previous_requests", session)


def append_to_last_request_dialog(user_id: str, role: str, message: str) -> None:
    """
    Appends a new message to the dialog of the last request session for a specific
    user. This function is useful for maintaining conversation history within a
    session. It retrieves the user's details, verifies the existence of prior
    requests, and appends the message to the dialog of the most recent session.
    If the user or relevant session details are missing, no action is performed.

    :param user_id: The unique identifier of the user whose session data is being
        updated.
    :param role: The role associated with the message, e.g., 'user' or 'system'.
    :param message: The content of the message to append to the session dialog.
    :return: None
    """
    user = get_user_by_id(user_id)
    if not user or "previous_requests" not in user or not user["previous_requests"]:
        return

    last_session = user["previous_requests"][-1]
    last_session["dialog"].append({"role": role, "message": message})

    logger.info(f"Appending message to session for user {user_id}")
    set_user_sessions(user_id, user["previous_requests"])


def get_conversation_history(user_id: str) -> list:
    """
    Retrieves the conversation history of a user by their user ID. The function
    fetches the user's previous requests and extracts the dialog messages in order
    to construct a structured conversation history. Messages with empty or missing
    content are excluded.

    :param user_id: The unique identifier of the user.
    :type user_id: str
    :return: A list of dictionaries representing the conversation history, where
             each dictionary contains the role (either 'assistant' or 'user') and
             the content of the message.
    :rtype: list
    """
    user = get_user_by_id(user_id)
    if not user:
        return []

    previous_requests = user.get("previous_requests", [])
    if not previous_requests:
        return []

    last_request = previous_requests[-1]
    dialog = last_request.get("dialog", [])

    history = []
    for entry in dialog:
        if "message" not in entry or not entry["message"].strip():
            continue
        role = "assistant" if entry["role"] == "bot" else "user"
        history.append({"role": role, "content": entry["message"]})

    return history


def get_user_sessions_summary(user_id: str) -> list[dict]:
    """
    Generate a summary of user sessions based on historical requests.

    This function retrieves a user's session data given their unique identifier
    and prepares a summarized list. Each summary entry contains the index of
    the session, the timestamp of the session, and a 50-character preview of
    the initial question asked in the session. If no user data or sessions exist,
    it returns an empty list.

    :param user_id: The unique identifier of the user whose sessions are
                    being summarized.
    :type user_id: str
    :return: A list of dictionaries where each dictionary summarizes a session,
             including the session index, its timestamp, and a preview of the
             initial question.
    :rtype: list[dict]
    """
    user = get_user_by_id(user_id)
    sessions = user.get("previous_requests", []) if user else []

    summary = []
    for idx, session in enumerate(sessions):
        timestamp = session.get("timestamp", "")
        question = session.get("initial_question", "")[:50]
        summary.append({"index": idx, "timestamp": timestamp, "preview": question})

    return summary


def delete_user_history(user_id: str) -> None:
    """
    Deletes the conversation history for a specified user. This function
    logs the deletion action and clears the history tied to the given
    user identifier.

    :param user_id: The ID of the user whose conversation history is
                    being deleted.
    :type user_id: str
    :returns: None
    """
    logger.info(f"Deleting conversation history for user {user_id}")
    clear_user_history(user_id)


def move_session_to_end(user_id: str, index: int) -> Optional[dict]:
    """
    Moves a session from a specified index in the user's sessions list to the end
    of the list. If the specified index is valid, the session is removed from its
    current position and appended to the end of the list. The sessions are then
    updated in the user's data store. If the index is invalid, no action is
    performed, and None is returned.

    :param user_id: The unique identifier of the user whose session is to be moved.
    :type user_id: str
    :param index: The zero-based index of the session to move.
    :type index: int
    :return: The session moved to the end if the index is valid, otherwise None.
    :rtype: Optional[dict]
    """
    user = get_user_by_id(user_id)
    sessions = user.get("previous_requests", []) if user else []

    if 0 <= index < len(sessions):
        session = sessions.pop(index)
        set_user_sessions(user_id, sessions + [session])
        logger.info(f"Moved session {index} to end for user {user_id}")
        return session

    return None


def has_accepted_agreement(user_id: str) -> bool:
    """
    Checks whether the user has accepted the agreement.

    Args:
        user_id (str): Telegram user ID

    Returns:
        bool: True if agreement_time is set, False otherwise
    """
    user = get_user_by_id(user_id)
    return bool(user and user.get("agreement_time"))


def update_last_session_rating(user_id: str, rating: str) -> None:
    """
    Updates the last session with a document rating.

    Args:
        user_id (str): Telegram user ID
        rating (str): User's rating text
    """
    user = get_user_by_id(user_id)
    if not user or "previous_requests" not in user or not user["previous_requests"]:
        logger.warning(f"No session found for user {user_id} to store rating")
        return

    last_session = user["previous_requests"][-1]
    last_session["document_rating"] = rating
    set_user_sessions(user_id, user["previous_requests"])
    logger.info(f"Stored document rating for user {user_id}")
