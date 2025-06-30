import logging
from pymongo import MongoClient
from typing import Dict, Optional
from config.config import MONGODB_URI, MONGODB_DB_NAME, MONGODB_COLLECTION_NAME

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

client = MongoClient(MONGODB_URI)
db = client[MONGODB_DB_NAME]
collection = db[MONGODB_COLLECTION_NAME]


def save_user(user_info: Dict) -> None:
    """
        Save or update a user document in MongoDB.

        Args:
            user_info (Dict): User information with "_id" as user ID.
        """
    try:
        collection.update_one(
            {"_id": user_info["_id"]},
            {
                "$set": {
                    "first_name": user_info.get("first_name"),
                    "last_name": user_info.get("last_name"),
                    "username": user_info.get("username"),
                },
                "$setOnInsert": {
                    "subscription_active": user_info.get("subscription_active", False),
                    "subscription_info": user_info.get("subscription_info", {}),
                    "previous_requests": user_info.get("previous_requests", []),
                    "agreement_time": user_info.get("agreement_time"),
                }
            },
            upsert=True
        )
        logger.info(f"User {user_info['_id']} saved/updated successfully")
    except Exception as e:
        logger.error(f"Failed to save user to DB: {e}", exc_info=True)


def get_user_by_id(user_id: str) -> Optional[Dict]:
    """
    Get full user data from MongoDB by Telegram ID.

    Args:
        user_id (int): Telegram user ID

    Returns:
        Optional[Dict]: User data dict or None if not found
    """
    try:
        user = collection.find_one({"_id": str(user_id)})
        logger.info(f"Retrieved user {user_id} from DB")
        return user
    except Exception as e:
        logger.error(f"Failed to retrieve user {user_id}: {e}", exc_info=True)
        return None


def update_user(user_id: str, update_fields: Dict) -> None:
    """
    Updates the user document in the database with the specified fields. The function
    performs an update operation using the provided user ID and a dictionary of fields
    to modify. Logs success or failure of the operation for tracking and debugging.

    :param user_id: The unique identifier of the user whose document needs to be updated.
    :type user_id: str
    :param update_fields: A dictionary containing the fields to update in the user document.
    :type update_fields: Dict
    :return: None
    :rtype: None
    """
    try:
        collection.update_one({"_id": user_id}, {"$set": update_fields})
        logger.info(f"Updated user {user_id} with fields {update_fields}")
    except Exception as e:
        logger.error(f"Failed to update user {user_id}: {e}", exc_info=True)


def push_to_user_array(user_id: str, array_field: str, data: Dict) -> None:
    """
    Pushes an item into a specified array field of a user's record in the database. This function
    targets a specific user identified by their unique identifier, modifies the identified
    array field by adding the provided data, and logs the success or failure of the operation.

    :param user_id: Unique identifier of the user whose record should be modified
    :type user_id: str
    :param array_field: Name of the array field within the user record to which data should be added
    :type array_field: str
    :param data: The item to be added to the user's array field
    :type data: Dict
    :return: None
    """
    try:
        collection.update_one({"_id": user_id}, {"$push": {array_field: data}})
        logger.info(f"Pushed data to user {user_id}'s array field {array_field}")
    except Exception as e:
        logger.error(f"Failed to push to user {user_id} array {array_field}: {e}", exc_info=True)


def get_all_active_users():
    """
    Fetch all users with active subscriptions.

    This function queries the database to retrieve all users whose subscriptions are
    marked as active. It logs the operation and returns the retrieved users if
    successful. If an error occurs during the database query, it logs the error,
    returns an empty list, and continues execution.

    :return: A list of active users or an empty list in case of an error.
    :rtype: list
    """
    try:
        users = collection.find({"subscription_active": True})
        logger.info("Fetched all active users")
        return users
    except Exception as e:
        logger.error("Failed to fetch active users", exc_info=True)
        return []


def clear_user_history(user_id: str) -> None:
    """
    Clears the user's interaction history.

    This function resets the given user's history by clearing their previous requests
    from the data storage. It is useful for resetting state associated with a user
    when starting fresh interactions or complying with data retention policies.

    :param user_id: The unique identifier for the user whose history is to be cleared
    :type user_id: str
    :return: None
    """
    logger.info(f"Clearing history for user {user_id}")
    update_user(user_id, {"previous_requests": []})


def set_user_sessions(user_id: str, sessions: list) -> None:
    """
    Updates the user sessions by setting the session data for the specified user ID.
    This function calls an update function to modify the 'previous_requests' key in
    the user's data with the provided list of sessions.

    :param user_id: The unique identifier of the user.
    :type user_id: str
    :param sessions: A list containing the session data to be set for the user.
    :type sessions: list
    :returns: None
    """
    logger.info(f"Setting sessions for user {user_id}")
    update_user(user_id, {"previous_requests": sessions})
