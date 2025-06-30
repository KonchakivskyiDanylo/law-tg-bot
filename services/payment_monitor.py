from datetime import datetime, timedelta
import asyncio
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class PaymentMonitor:
    """
    PaymentMonitor is responsible for maintaining, tracking, and processing
    pending payments, ensuring seamless integration with external services for
    payment verification and subscription updates.

    This class provides mechanisms to add new pending payments into a queue,
    monitor their statuses, handle different payment outcomes (successful,
    canceled, or pending) effectively, and interact with an external Telegram
    application to notify users regarding updates. It automates the process of
    identifying expired payments, updating subscriptions for processed payments,
    and communicating status updates to users.

    It employs asynchronous operations for payment status monitoring and notification
    dispatch, leveraging asyncio for efficient and scalable periodic processing.

    :ivar application: Telegram application instance for bot communication.
    :type application: Optional[Any]
    :ivar pending_payments: A dictionary storing pending payment data.
    :type pending_payments: dict
    :ivar _monitoring_task: An asyncio.Task for monitoring payments.
    :type _monitoring_task: Optional[asyncio.Task]
    :ivar _is_monitoring: Flag to indicate whether monitoring is active or not.
    :type _is_monitoring: bool
    """

    def __init__(self, application=None):
        self.application = application
        self.pending_payments = {}
        self._monitoring_task = None
        self._is_monitoring = False

    def set_application(self, application):
        """
        Sets the application instance used by the payment monitor.

        This method assigns the provided application to the `application` attribute
        of the instance. Upon successfully setting the application, it logs the
        action to confirm that the Telegram application is configured for the
        payment monitoring system.

        :param application: The Telegram application instance to be set.
        :type application: Any
        :return: None
        """
        self.application = application
        logger.info("Telegram application set for payment monitor")

    def add_pending_payment(self, payment_id: str, user_id: str, tariff_type: str):
        """
        Adds a new pending payment to the monitoring queue and starts monitoring
        if not already active.

        This method registers a pending payment identified by the given payment_id
        for a specific user and tariff type. It stores the payment information within
        the pending payments data structure and triggers the monitoring process if it
        is currently inactive.

        :param payment_id: Identifier of the payment to be added
        :param user_id: Identifier of the user associated with the payment
        :param tariff_type: Type of tariff plan associated with the payment
        :return: None
        """
        self.pending_payments[payment_id] = {
            "user_id": user_id,
            "tariff_type": tariff_type,
            "created_at": datetime.now()
        }
        logger.info(f"Added payment {payment_id} for user {user_id} (tariff: {tariff_type}) to monitoring queue")

        if not self._is_monitoring:
            asyncio.create_task(self.initialize_monitoring())

    async def check_pending_payments(self):
        """
        Checks the status of pending payments and processes them accordingly. If no pending
        payments exist, the method logs and exits. It manages expired payments and processes
        them based on their statuses: succeeded, canceled, or still pending. Handles necessary
        imports dynamically and logs errors in case of failures.

        :raises ImportError: If required modules for payment handling cannot be imported.
        :raises Exception: If an error occurs while checking or processing payments.
        """
        if not self.pending_payments:
            return

        logger.info(f"Checking {len(self.pending_payments)} pending payments")

        try:
            from services.yookassa_service import check_payment_status
            from services.subscription_service import update_subscription, update_payment_method
        except ImportError as e:
            logger.error(f"Import error in payment check: {e}")
            return

        for payment_id in list(self.pending_payments.keys()):
            payment_info = self.pending_payments[payment_id]

            logger.info(f"Checking payment {payment_id} for user {payment_info['user_id']}")

            if datetime.now() - payment_info["created_at"] > timedelta(minutes=10):
                del self.pending_payments[payment_id]
                logger.info(f"Payment {payment_id} expired (>24h), removed from queue")
                continue

            try:
                payment_data = check_payment_status(payment_id)
                logger.info(f"Payment {payment_id} status: {payment_data.get('status') if payment_data else 'None'}")

                if not payment_data:
                    logger.warning(f"No payment data returned for {payment_id}")
                    continue

                if payment_data["status"] == "succeeded":
                    await self._process_successful_payment(payment_id, payment_info, payment_data)

                elif payment_data["status"] == "canceled":
                    await self._process_canceled_payment(payment_id, payment_info)

                elif payment_data["status"] == "pending":
                    logger.info(f"Payment {payment_id} still pending")

                else:
                    logger.warning(f"Unknown payment status for {payment_id}: {payment_data['status']}")

            except Exception as e:
                logger.error(f"Error checking payment {payment_id}: {e}")

    async def _process_successful_payment(self, payment_id: str, payment_info: dict, payment_data: dict):
        """
        Processes a successful payment by updating subscription details, optionally updating
        the payment method, sending a notification to the user, and removing the payment
        from the list of pending payments.

        :param payment_id: The unique identifier of the payment.
        :type payment_id: str
        :param payment_info: A dictionary containing information about the payment such
            as user ID and tariff type.
        :type payment_info: dict
        :param payment_data: A dictionary containing additional data about the payment,
            such as subscription start date and payment method ID.
        :type payment_data: dict
        :return: None
        """
        try:
            from services.subscription_service import update_subscription, update_payment_method
            from prompts import SUBSCRIPTION_SUCCESS_MESSAGE
        except ImportError as e:
            logger.error(f"Import error in successful payment processing: {e}")
            return

        user_id = payment_info["user_id"]
        tariff_type = payment_info["tariff_type"]

        logger.info(f"Processing successful payment {payment_id} for user {user_id}")

        try:
            update_subscription(
                user_id,
                sub_type=tariff_type,
                start=payment_data.get("subscription_start")
            )

            if payment_data.get("payment_method_id"):
                update_payment_method(user_id, payment_data["payment_method_id"])

            logger.info(f"Subscription updated for user {user_id}")
        except Exception as e:
            logger.error(f"Failed to update subscription for user {user_id}: {e}")
            return

        await self._send_success_notification(user_id, tariff_type, payment_data)

        del self.pending_payments[payment_id]
        logger.info(f"Payment {payment_id} processed successfully for user {user_id}")

    async def _process_canceled_payment(self, payment_id: str, payment_info: dict):
        """
        Handles processing of a cancelled payment by removing it from pending payments
        and notifying the user about the cancellation.

        :param payment_id: Unique identifier of the payment being processed.
        :type payment_id: str
        :param payment_info: Dictionary containing payment details including user-specific
                             information like user_id.
        :type payment_info: dict
        :return: None
        """
        del self.pending_payments[payment_id]
        logger.info(f"Payment {payment_id} was canceled")

        if self.application:
            try:
                await self.application.bot.send_message(
                    chat_id=payment_info["user_id"],
                    text="❌ Payment was canceled. You can try subscribing again.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to send cancellation message: {e}")

    async def _send_success_notification(self, user_id: str, tariff_type: str, payment_data: dict):
        """
        Sends a subscription success notification to the user via Telegram. The notification
        includes details such as tariff type and subscription start and end dates. This method
        requires a valid Telegram application instance to send the message. If the application
        is not set or there are issues with importing the success message prompt, the
        notification will not be sent. Additionally, ensures detailed logging of sending
        operations or any issues encountered.

        :param user_id: Unique identifier of the Telegram user to whom the notification will
            be sent.
        :type user_id: str
        :param tariff_type: Type of the subscription plan purchased by the user. Can be one of
            the predefined tariff types such as "basic" or "premium".
        :type tariff_type: str
        :param payment_data: Dictionary containing payment and subscription information, such
            as start and end dates of the subscription.
        :type payment_data: dict
        :return: None. The function performs an asynchronous operation to send a message and
            does not return any value.
        :rtype: None
        """
        if not self.application:
            logger.error("Cannot send notification: Telegram application not set")
            return

        try:
            from prompts import SUBSCRIPTION_SUCCESS_MESSAGE
        except ImportError as e:
            logger.error(f"Import error for success message: {e}")
            return

        tariff_names = {"basic": "Consultation", "premium": "Basic"}
        tariff_name = tariff_names.get(tariff_type, "Unknown")

        message_text = SUBSCRIPTION_SUCCESS_MESSAGE.format(
            tariff_name=tariff_name,
            subscription_start=payment_data.get("subscription_start", "today"),
            subscription_end=payment_data.get("subscription_end", "in a month")
        )

        try:
            await self.application.bot.send_message(
                chat_id=user_id,
                text=message_text,
                parse_mode="Markdown"
            )
            logger.info(f"Success message sent to user {user_id}")
        except Exception as e:
            logger.error(f"Failed to send success message to user {user_id}: {e}")

    async def start_monitoring(self):
        """
        Starts the monitoring process for payments. Continuously checks for pending
        payments and dynamically adjusts the monitoring interval depending on the presence
        of payments to be monitored. In case of an error during monitoring, it temporarily
        pauses before resuming operations.

        :raises Exception: Logs the error encountered while monitoring and waits for
                           a defined recovery time before retrying.
        """
        self._is_monitoring = True
        logger.info("Payment monitoring started")

        while self._is_monitoring:
            try:
                await self.check_pending_payments()
                if not self.pending_payments:
                    await asyncio.sleep(60)
                else:
                    await asyncio.sleep(15)

            except Exception as e:
                logger.error(f"Error in payment monitoring: {e}")
                await asyncio.sleep(30)

    async def initialize_monitoring(self):
        """
        Initializes the monitoring process for payment operations.

        This method sets up a monitoring task using asyncio if the monitoring is not
        already active and the application is initialized. It ensures the system remains
        ready to monitor payments under appropriate conditions.

        :return: None
        """
        if not self._is_monitoring and self.application:
            self._monitoring_task = asyncio.create_task(self.start_monitoring())
            logger.info("Payment monitoring task created and started")
        elif not self.application:
            logger.warning("Cannot start monitoring: Telegram application not set")

    def stop_monitoring(self):
        """
        Stops the payment monitoring process by canceling the active monitoring task
        and setting the monitoring state to False. This ensures no resources are being
        used for monitoring when it is no longer required.

        :return: None
        """
        self._is_monitoring = False
        if self._monitoring_task and not self._monitoring_task.done():
            self._monitoring_task.cancel()
            logger.info("Payment monitoring stopped")

    def get_pending_count(self) -> int:
        """
        Calculate the count of pending payments.

        This method returns the number of payments that are currently outstanding
        and awaiting processing. It leverages the stored `pending_payments` list
        to determine the count of unprocessed payment entries.

        :return: The total count of pending payments.
        :rtype: int
        """
        return len(self.pending_payments)

    def get_user_pending_payments(self, user_id: str) -> list:
        """
        Retrieves a list of pending payments associated with the specified user. The method
        filters through the internal pending payments data structure and compiles a list of
        payment details, including the payment ID, associated tariff type, creation timestamp,
        and the age of the payment in minutes.

        :param user_id: The unique identifier of the user whose pending payments are
            to be retrieved.
        :return: A list of dictionaries, where each dictionary contains details of a
            user's pending payment, including the payment ID, tariff type, timestamp of
            creation, and age in minutes.
        """
        return [
            {
                "payment_id": payment_id,
                "tariff_type": payment_info["tariff_type"],
                "created_at": payment_info["created_at"],
                "age_minutes": (datetime.now() - payment_info["created_at"]).total_seconds() / 60
            }
            for payment_id, payment_info in self.pending_payments.items()
            if payment_info["user_id"] == user_id
        ]

    def force_check_payment(self, payment_id: str = None):
        """
        Initiates a forceful payment check for a specific payment ID or all pending payments.

        If a specific `payment_id` is provided and exists in the pending payments,
        the corresponding payment is checked individually. Otherwise, all pending
        payments are checked.

        :param payment_id: The identifier of the specific payment to be force-checked.
                           If None, all pending payments will be checked.
        :type payment_id: str, optional
        :return: None
        """
        if payment_id and payment_id in self.pending_payments:
            logger.info(f"Force checking payment {payment_id}")
            asyncio.create_task(self._check_single_payment(payment_id))
        else:
            logger.info("Force checking all pending payments")
            asyncio.create_task(self.check_pending_payments())

    async def _check_single_payment(self, payment_id: str):
        """
        Asynchronously performs a check on a single pending payment by isolating it from
        other pending payments temporarily. After processing, the original set of payments
        is restored, ensuring that previously processed payments are not re-added.

        :param payment_id: Unique identifier of the payment to be checked.
        :type payment_id: str
        :return: None
        :rtype: None
        """
        if payment_id not in self.pending_payments:
            logger.warning(f"Payment {payment_id} not found in pending payments")
            return

        temp_payments = {payment_id: self.pending_payments[payment_id]}
        original_payments = self.pending_payments.copy()
        self.pending_payments = temp_payments

        try:
            await self.check_pending_payments()
        finally:
            for pid, pinfo in original_payments.items():
                if pid not in self.pending_payments:
                    self.pending_payments[pid] = pinfo


_payment_monitor_instance: Optional[PaymentMonitor] = None


def initialize_payment_monitor(application=None) -> PaymentMonitor:
    """
    Initializes a singleton instance of the PaymentMonitor class. If a global instance of the
    monitor does not exist, it creates one with the given application. If a global instance
    already exists but does not have an application assigned, it assigns the provided
    application to the monitor.

    :param application: An optional application configuration passed to the PaymentMonitor
        instance. If not provided, the monitor will be initialized without an application
        unless a global instance already exists with an application set.
    :type application: Optional[Any]
    :return: The singleton instance of the PaymentMonitor.
    :rtype: PaymentMonitor
    """
    global _payment_monitor_instance

    if _payment_monitor_instance is None:
        _payment_monitor_instance = PaymentMonitor(application)
        logger.info("Payment monitor initialized")
    elif application and not _payment_monitor_instance.application:
        _payment_monitor_instance.set_application(application)

    return _payment_monitor_instance


def get_payment_monitor() -> Optional[PaymentMonitor]:
    """
    Retrieve the global instance of the payment monitor.

    This function accesses the single global instance of the payment monitor. If the
    payment monitor has not been initialized prior to calling this function, a warning
    message is logged, and the function will return None. Ensure `initialize_payment_monitor()`
    has been invoked before attempting to retrieve the payment monitor instance.

    :return: The global instance of the payment monitor if initialized, or None.
    :rtype: Optional[PaymentMonitor]
    """
    global _payment_monitor_instance

    if _payment_monitor_instance is None:
        logger.warning("Payment monitor not initialized! Call initialize_payment_monitor() first.")

    return _payment_monitor_instance


def add_payment_to_monitor(payment_id: str, user_id: str, tariff_type: str) -> bool:
    """
    Adds a payment to the monitoring system.

    This function interacts with the payment monitoring system to add a payment
    to its pending queue. It requires the payment's unique identifier, the user
    to whom the payment belongs, and the type of tariff for the payment. The
    function ensures that the payment monitor is properly initialized before
    attempting to add the payment. If the monitor is not initialized, it logs an
    error and returns a failure indicator.

    :param payment_id: Unique identifier of the payment.
    :type payment_id: str
    :param user_id: Unique identifier of the user associated with the payment.
    :type user_id: str
    :param tariff_type: Type or category of the tariff associated with the payment.
    :type tariff_type: str
    :return: True if the payment was successfully added to the monitor, False
        otherwise.
    :rtype: bool
    """
    monitor = get_payment_monitor()
    if monitor:
        monitor.add_pending_payment(payment_id, user_id, tariff_type)
        return True
    else:
        logger.error("Cannot add payment to monitor: monitor not initialized")
        return False


async def check_user_payments(user_id: str) -> dict:
    """
    Checks pending payments for a given user using the payment monitor.

    This asynchronous function interacts with a payment monitor to check
    and retrieve any pending payments for a provided user ID. The function
    ensures that the monitor is operational before proceeding to check for
    pending payments and fetches the results related to the specified user.

    :param user_id: The unique identifier of the user whose pending payments
        are to be checked.
    :type user_id: str
    :return: A dictionary containing the user's ID, a list of their pending
        payments, and the total count of those payments. If the monitor is
        not initialized, returns an error message in the response.
    :rtype: dict
    """
    monitor = get_payment_monitor()
    if not monitor:
        return {"error": "Monitor not initialized"}

    await monitor.check_pending_payments()
    user_payments = monitor.get_user_pending_payments(user_id)

    return {
        "user_id": user_id,
        "pending_payments": user_payments,
        "total_pending": len(user_payments)
    }
