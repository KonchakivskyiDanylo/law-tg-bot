"""
YooKassa payment integration for Telegram bot subscriptions.
Handles payment creation, status checking, and recurring payment support.
"""

import logging
from datetime import datetime, timedelta
from uuid import uuid4
from yookassa import Configuration, Payment
from config.config import (
    YOOKASSA_SHOP_ID,
    YOOKASSA_SECRET_KEY,
    BOT_URL,
    TARIFF_PRICES,
    SUBSCRIPTION_DURATION_DAYS
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

try:
    Configuration.account_id = YOOKASSA_SHOP_ID
    Configuration.secret_key = YOOKASSA_SECRET_KEY
except Exception as e:
    logger.error(f"Failed to initialize YooKassa: {e}")


def create_payment(user_id, tariff_type, return_url=None):
    """
    Creates a payment for a specified user and tariff type. The function generates
    a payment record with the corresponding details such as amount, currency, and
    confirmation type. It also includes metadata for the user and tariff type. If
    an invalid tariff type is provided, the function logs a warning and returns
    None. The function attempts to create a payment using an idempotence key
    and returns the payment ID, confirmation URL, and status upon successful
    creation.

    :param user_id: The unique identifier of the user for whom the payment is
                    being created.
    :type user_id: str

    :param tariff_type: The type of tariff for which the payment is being
                         processed. It determines the payment amount.
    :type tariff_type: str

    :param return_url: An optional URL to which the user will be redirected upon
                       successful payment. If not provided, a default URL is used.
    :type return_url: str or None

    :return: A dictionary containing the payment ID, confirmation URL, and payment
             status if creation is successful, otherwise None.
    :rtype: dict or None
    """
    price = TARIFF_PRICES.get(tariff_type)
    if not price:
        logger.warning(f"Invalid tariff type provided: {tariff_type}")
        return None
    try:
        idempotence_key = str(uuid4())

        payment_data = {
            "amount": {
                "value": str(price),
                "currency": "USD"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": return_url or f"{BOT_URL}?start=payment_success"
            },
            "capture": True,
            "description": f"{tariff_type.capitalize()} subscription for the legal bot",
            "metadata": {
                "user_id": user_id,
                "tariff_type": tariff_type
            },
            "save_payment_method": True
        }

        payment = Payment.create(payment_data, idempotence_key)
        logger.info(f"Payment created for user {user_id}, type {tariff_type}, id={payment.id}")
        return {
            "payment_id": payment.id,
            "confirmation_url": payment.confirmation.confirmation_url,
            "status": payment.status
        }
    except Exception as e:
        logger.error(f"Failed to create payment for user {user_id}: {e}", exc_info=True)
        return None


def check_payment_status(payment_id):
    """
    Checks the status of a payment and returns details about the payment and
    its associated metadata. If the payment has succeeded, additional
    information related to the user's subscription is calculated and returned.
    Handles exceptions during the process and logs any encountered errors.

    :param payment_id: The unique identifier of the payment to check.
    :type payment_id: str
    :return: A dictionary containing payment status, user information,
             subscription details (if applicable), and payment method ID, or
             None in case of an error.
    :rtype: dict or None
    """
    try:
        payment = Payment.find_one(payment_id)

        if payment.status == "succeeded":
            metadata = payment.metadata
            user_id = metadata.get("user_id")
            tariff_type = metadata.get("tariff_type")

            start_date = datetime.now()
            end_date = start_date + timedelta(days=SUBSCRIPTION_DURATION_DAYS)

            logger.info(f"Payment {payment_id} succeeded for user {user_id} ({tariff_type})")
            return {
                "status": "succeeded",
                "user_id": user_id,
                "tariff_type": tariff_type,
                "subscription_start": start_date.strftime("%d.%m.%Y"),
                "subscription_end": end_date.strftime("%d.%m.%Y"),
                "payment_method_id": getattr(payment.payment_method, "id", None)
            }

        return {
            "status": payment.status,
            "user_id": payment.metadata.get("user_id") if payment.metadata else None
        }
    except Exception as e:
        logger.error(f"Failed to check payment status for {payment_id}: {e}", exc_info=True)
        return None


def create_recurring_payment(user_id, tariff_type, payment_method_id):
    """
    Creates a recurring payment for a user, based on the specified tariff type and payment method.
    This function uses preset tariff prices and builds a payment request with required metadata
    for processing a subscription auto-renewal.

    :param user_id: Unique identifier of the user
                   for whom the recurring payment is to be created.
    :type user_id: str
    :param tariff_type: The subscription type for which the recurring payment is being created.
                        This determines the price of the subscription.
    :type tariff_type: str
    :param payment_method_id: Identifier of the payment method that will be used for the recurring payment.
    :type payment_method_id: str
    :return: A dictionary containing the payment ID and status of the created payment,
             or None if the payment could not be created.
    :rtype: dict | None
    """
    price = TARIFF_PRICES.get(tariff_type)
    if not price:
        logger.warning(f"Invalid tariff type provided: {tariff_type}")
        return None
    try:
        idempotence_key = str(uuid4())

        payment_data = {
            "amount": {
                "value": str(price),
                "currency": "USD"
            },
            "capture": True,
            "payment_method_id": payment_method_id,
            "description": f"Auto-renewal of {tariff_type.capitalize()} subscription for legal bot",
            "metadata": {
                "user_id": user_id,
                "tariff_type": tariff_type,
                "is_recurring": True
            }
        }

        payment = Payment.create(payment_data, idempotence_key)
        logger.info(f"Recurring payment created for user {user_id}, tariff {tariff_type}, payment_id={payment.id}")
        return {
            "payment_id": payment.id,
            "status": payment.status
        }
    except Exception as e:
        logger.error(f"Failed to create recurring payment for user {user_id}: {e}", exc_info=True)
        return None
