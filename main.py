"""
Legal Support Telegram Bot

This module is the main entry point for the Telegram bot application.
It sets up the Telegram application, registers all command and callback handlers,
initializes the scheduler, and starts the polling loop.
"""
import asyncio
import logging
import os
from datetime import datetime
from dotenv import load_dotenv
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters
)

from apscheduler.schedulers.background import BackgroundScheduler

from config.config import TELEGRAM_BOT_TOKEN
from handlers.command_handlers import (
    start,
    handle_new_subscription,
    handle_my_subscription,
    handle_ask,
    handle_create_document,
    menu_command,
    handle_tariff_selection,
    process_yookassa_webhook,
    handle_change_tariff,
    handle_check_payment,
    handle_history_callbacks,
    handle_history,
    handle_rate_document,
    handle_accept_code,
    handle_create_document_from_response
)
from handlers.message_handlers import handle_message
from services.subscription_service import check_subscriptions
from services.payment_monitor import initialize_payment_monitor, get_payment_monitor

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def post_init(application: Application) -> None:
    """
    Initializes the payment monitoring system by associating it with the given
    application and setting up monitoring if a payment monitor is available. Logs
    an appropriate message based on the outcome of the initialization process.

    :param application: The application instance to link with the payment monitor
    :type application: Application
    :return: None
    :rtype: None
    """
    payment_monitor = get_payment_monitor()
    if payment_monitor:
        payment_monitor.set_application(application)
        await payment_monitor.initialize_monitoring()
        logger.info("Payment monitor initialized in post_init")
    else:
        logger.error("Payment monitor not found in post_init")


def configure_handlers(app: Application) -> None:
    """
    Configures various command and callback handlers for a Telegram bot application by
    adding them to the provided application instance. This allows the application to
    respond to specific commands, callbacks, and messages according to predefined logic.
    Includes functionality for debugging payments, handling different user commands,
    interacting with payment systems, and processing various user inputs.

    :param app: The `Application` instance for the Telegram bot to which handlers
        are added. This instance represents the bot and manages the lifecycle of
        different handlers and their triggers.
    :type app: Application
    :return: None
    """
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_command))

    async def debug_payments(update, context):
        YOUR_ADMIN_ID = os.getenv("ADMIN_ID")

        if update.effective_user.id == YOUR_ADMIN_ID:
            payment_monitor = get_payment_monitor()
            if payment_monitor:
                await payment_monitor.check_pending_payments()
                pending_count = payment_monitor.get_pending_count()
                await update.message.reply_text(f"✅ Checked. Pending payments: {pending_count}")

                if pending_count > 0:
                    details = []
                    for payment_id, info in payment_monitor.pending_payments.items():
                        age_minutes = (datetime.now() - info["created_at"]).total_seconds() / 60
                        details.append(f"• {payment_id[:8]}... (user: {info['user_id']}, age: {age_minutes:.1f}m)")

                    await update.message.reply_text(f"Details:\n" + "\n".join(details[:10]))
                else:
                    await update.message.reply_text("❌ Payment monitor is not initialized")
            else:
                await update.message.reply_text("❌ You don't have permission to execute this command")

    app.add_handler(CommandHandler("debug_payments", debug_payments))
    app.add_handler(CallbackQueryHandler(handle_accept_code, pattern="^accept_code$"))
    app.add_handler(CallbackQueryHandler(start, pattern="^accept_code$"))
    app.add_handler(CallbackQueryHandler(handle_ask, pattern="^main_ask$"))
    app.add_handler(CallbackQueryHandler(handle_create_document, pattern="^main_document$"))
    app.add_handler(CallbackQueryHandler(handle_history, pattern="^main_history$"))
    app.add_handler(CallbackQueryHandler(handle_new_subscription, pattern="^main_new_subscription$"))
    app.add_handler(CallbackQueryHandler(handle_my_subscription, pattern="^main_my_subscription$"))
    app.add_handler(CallbackQueryHandler(menu_command, pattern="^back_to_menu$"))
    app.add_handler(CallbackQueryHandler(handle_tariff_selection, pattern="^tariff_(basic|premium)$"))
    app.add_handler(CallbackQueryHandler(handle_change_tariff, pattern="^change_tariff$"))
    app.add_handler(CallbackQueryHandler(handle_check_payment, pattern="^check_payment$"))
    app.add_handler(CallbackQueryHandler(handle_rate_document, pattern="^rate_document$"))
    app.add_handler(
        CallbackQueryHandler(handle_create_document_from_response, pattern="^create_document_from_response$"))

    app.add_handler(CallbackQueryHandler(
        handle_history_callbacks,
        pattern="^(history_open_|history_delete_confirm|history_delete|history_cancel)"
    ))

    app.add_handler(MessageHandler(
        filters.StatusUpdate.WEB_APP_DATA & filters.Regex(r"yookassa"),
        process_yookassa_webhook
    ))

    app.add_handler(MessageHandler(filters.TEXT | filters.Document.ALL, handle_message))


def setup_scheduler(app: Application) -> None:
    """
    Sets up and starts a scheduler to automate specific recurring tasks such as
    subscription and payment checks.

    The scheduler is responsible for performing daily checks on subscriptions
    and periodic checks for pending payments. It ensures that these tasks are
    executed asynchronously while managing any potential exceptions.

    :param app: An instance of the application that provides necessary
                context or resources for the scheduled tasks.
    :type app: Application
    :return: None
    """
    scheduler = BackgroundScheduler()

    def run_check_subscriptions():
        """
        Sets up a scheduled task for checking subscriptions and integrates it into the provided
        application. The function creates an internal routine to periodically execute the
        `check_subscriptions` coroutine, ensuring proper event loop handling and error logging.

        :param app: The application instance to which the scheduler will be integrated.
        :type app: Application
        """
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(check_subscriptions(app))
            loop.close()
        except Exception as e:
            logger.error(f"Error in scheduled subscription check: {e}")

    scheduler.add_job(run_check_subscriptions, "cron", hour=12, minute=0)

    def run_payment_check():
        """
        Sets up a scheduler for checking pending payments within the given application. The function initiates
        a scheduled task that invokes a `payment_monitor` to check and process pending payments if available.
        This is achieved through an asynchronous event loop to handle potentially long-running operations.

        :param app: The main application instance where the scheduler is to be configured.
        :type app: Application

        :return: None
        """
        try:
            payment_monitor = get_payment_monitor()
            if payment_monitor and payment_monitor.get_pending_count() > 0:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(payment_monitor.check_pending_payments())
                loop.close()
        except Exception as e:
            logger.error(f"Error in scheduled payment check: {e}")

    scheduler.add_job(run_payment_check, "interval", minutes=3)

    scheduler.start()
    logger.info("Scheduler started with payment checks every 3 minutes")


def main() -> None:
    """
    This function serves as the entry point for initializing and starting the
    Telegram bot application with specified configurations and handlers. It is
    responsible for setting up the application, including the Telegram bot
    token, initializing payment monitoring, configuring handlers, and scheduling
    tasks. Additionally, it incorporates a post-initialization step and runs the
    bot to start polling for updates.

    :return: None
    """
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    initialize_payment_monitor()
    logger.info("Payment monitor pre-initialized")

    configure_handlers(application)

    setup_scheduler(application)

    application.post_init = post_init

    logger.info("Bot is starting...")
    application.run_polling()


if __name__ == "__main__":
    main()
