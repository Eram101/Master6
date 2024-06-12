import os
import subprocess
import time
import pickle
from concurrent.futures import ThreadPoolExecutor
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

# Set your Telegram bot token here
TELEGRAM_BOT_TOKEN = "6986622662:AAEcaJWizB9Rpy_zdmBJcHxr6lU_HddGMOk"

# Directory paths
UPLOADS_DIR = "uploads"
OUTPUTS_DIR = "outputs"

# Ensure necessary directories exist
for directory in [UPLOADS_DIR, OUTPUTS_DIR]:
    os.makedirs(directory, exist_ok=True)

# Sets for tracking user roles and processing states
ADMIN_USER_IDS = {6023294627, 5577750831, 1187810967}
REGULAR_USER_IDS = set()
user_timers = {}
processed_domains = {}
file_queue = []
processing_now = False
global_free_access = 0

executor = ThreadPoolExecutor(max_workers=5)

STATE_FILE = 'processing_state.pkl'

def save_state():
    with open(STATE_FILE, 'wb') as f:
        state = {
            'file_queue': file_queue,
            'processing_now': processing_now,
            'processed_domains': processed_domains,
            'user_timers': user_timers,
            'global_free_access': global_free_access,
        }
        pickle.dump(state, f)

def load_state():
    global file_queue, processing_now, processed_domains, user_timers, global_free_access
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'rb') as f:
            state = pickle.load(f)
            file_queue = state.get('file_queue', [])
            processing_now = state.get('processing_now', False)
            processed_domains = state.get('processed_domains', {})
            user_timers = state.get('user_timers', {})
            global_free_access = state.get('global_free_access', 0)

def start(update: Update, context: CallbackContext) -> None:
    """Handles the /start command."""
    user_id = update.message.from_user.id
    role = "Admin" if user_id in ADMIN_USER_IDS else "Regular User" if user_id in REGULAR_USER_IDS or global_free_access > time.time() else "Unauthorized User"

    update.message.reply_text(
        f'Welcome to Subdomain Enumeration Bot!\n'
        f'Send me a file with domains or a single domain to get started.\n'
        f'Your role: {role}\n'
        f'To check for remaining time use this /timeleft.'
    )

def process_file(file_entry, context: CallbackContext) -> None:
    global processing_now

    try:
        file_name = file_entry["file_name"]
        chat_id = file_entry["chat_id"]
        output_file_name = f"{file_name.split('.')[0]}_sub-domains.txt"
        output_file_path = os.path.join(OUTPUTS_DIR, output_file_name)

        with open(os.path.join(UPLOADS_DIR, file_name), 'r') as file:
            domains = file.read().splitlines()

        new_domains = [domain for domain in domains if domain not in processed_domains]

        if not new_domains:
            context.bot.send_message(chat_id, "No new domains to process.")
            for domain in domains:
                if domain in processed_domains:
                    context.bot.send_document(chat_id, document=open(processed_domains[domain], 'rb'))
            return

        subprocess.check_call([
            'subfinder', '-dL',
            os.path.join(UPLOADS_DIR, file_name), '-o', output_file_path
        ])

        context.bot.send_document(chat_id, document=open(output_file_path, 'rb'))

        for domain in new_domains:
            processed_domains[domain] = output_file_path

        os.remove(os.path.join(UPLOADS_DIR, file_name))
        
        save_state()  # Save state after processing

    except Exception as e:
        context.bot.send_message(chat_id, f"Error during enumeration: {e}")

    finally:
        process_file_queue(context)

def process_file_queue(context: CallbackContext) -> None:
    global processing_now
    if file_queue:
        file_entry = file_queue.pop(0)
        executor.submit(process_file, file_entry, context)
        save_state()  # Save state after queue update
    else:
        processing_now = False

def add_user(update: Update, context: CallbackContext) -> None:
    """Adds a user with limited access."""
    user_id = update.message.from_user.id

    if user_id in ADMIN_USER_IDS:
        try:
            target_user_id = int(context.args[0])
            duration_seconds = int(context.args[1])
            user_timers[target_user_id] = time.time() + duration_seconds
            save_state()
            update.message.reply_text(f"User {target_user_id} added with access for {duration_seconds} seconds.")
        except (ValueError, IndexError):
            update.message.reply_text("Invalid command. Use /add (user_id) (duration_seconds)")
    else:
        update.message.reply_text("You are not authorized to use this command.")

def time_left(update: Update, context: CallbackContext) -> None:
    """Checks the remaining time for the user."""
    user_id = update.message.from_user.id

    if user_id in user_timers:
        remaining_time = user_timers[user_id] - time.time()
        if remaining_time > 0:
            update.message.reply_text(f"Time remaining: {int(remaining_time)} seconds.")
        else:
            update.message.reply_text("Your access has expired.")
    elif global_free_access > time.time():
        remaining_time = global_free_access - time.time()
        update.message.reply_text(f"Global free access time remaining: {int(remaining_time)} seconds.")
    else:
        update.message.reply_text("You do not have access.")

def handle_document(update: Update, context: CallbackContext) -> None:
    """Handles document uploads."""
    user_id = update.message.from_user.id

    if not is_user_authorized(user_id):
        update.message.reply_text("You are not authorized to use this bot.")
        return

    document = update.message.document
    file = document.get_file()
    file_name = document.file_name
    file_path = os.path.join(UPLOADS_DIR, file_name)

    file.download(file_path)

    file_queue.append({"file_name": file_name, "chat_id": update.message.chat_id})
    save_state()

    update.message.reply_text(f"File {file_name} received. Processing will start shortly.")
    if not processing_now:
        process_file_queue(context)

def handle_text(update: Update, context: CallbackContext) -> None:
    """Handles text messages (for single domain processing)."""
    user_id = update.message.from_user.id

    if not is_user_authorized(user_id):
        update.message.reply_text("You are not authorized to use this bot.")
        return

    domain = update.message.text.strip()
    file_name = f"{domain.replace('.', '_')}.txt"
    file_path = os.path.join(UPLOADS_DIR, file_name)

    with open(file_path, 'w') as file:
        file.write(domain)

    file_queue.append({"file_name": file_name, "chat_id": update.message.chat_id})
    save_state()

    update.message.reply_text(f"Domain {domain} received. Processing will start shortly.")
    if not processing_now:
        process_file_queue(context)

def view_processed_domains(update: Update, context: CallbackContext) -> None:
    """Shows the processed domains."""
    update.message.reply_text("Processed domains: " + ", ".join(processed_domains.keys()))

def clear_processed_domains(update: Update, context: CallbackContext) -> None:
    """Clears the processed domains."""
    processed_domains.clear()
    save_state()
    update.message.reply_text("Processed domains cleared.")

def list_users(update: Update, context: CallbackContext) -> None:
    """Lists users and their access time."""
    user_id = update.message.from_user.id

    if user_id in ADMIN_USER_IDS:
        users_info = "\n".join([f"User {uid}: {int(user_timers[uid] - time.time())} seconds left" for uid in user_timers if time.time() < user_timers[uid]])
        update.message.reply_text(f"Users and remaining access time:\n{users_info}")
    else:
        update.message.reply_text("You are not authorized to use this command.")

def free_access(update: Update, context: CallbackContext) -> None:
    """Grants free access to all users for a specified duration."""
    global global_free_access  # Declare global variable

    user_id = update.message.from_user.id

    if user_id in ADMIN_USER_IDS:
        try:
            duration_seconds = int(context.args[0])
            global_free_access = time.time() + duration_seconds
            save_state()
            update.message.reply_text(f"Free access granted to all users for {duration_seconds} seconds.")
        except (ValueError, IndexError):
            update.message.reply_text("Invalid command. Use /free (duration_in_seconds)")
    else:
        update.message.reply_text("You are not authorized to use this command.")

def broadcast(update: Update, context: CallbackContext) -> None:
    """Broadcasts a message to all users."""
    user_id = update.message.from_user.id

    if user_id in ADMIN_USER_IDS:
        message = " ".join(context.args)
        if message:
            for user_id in REGULAR_USER_IDS:
                context.bot.send_message(user_id, message)
            update.message.reply_text("Broadcast message sent.")
        else:
            update.message.reply_text("Message is empty.")
    else:
        update.message.reply_text("You are not authorized to use this command.")

def active_users(update: Update, context: CallbackContext) -> None:
    """Counts the number of active users."""
    user_id = update.message.from_user.id

    if user_id in ADMIN_USER_IDS:
        active_user_count = sum(1 for user_id in REGULAR_USER_IDS if user_id in user_timers and time.time() < user_timers[user_id])
        update.message.reply_text(f"Number of active users: {active_user_count}")
    else:
        update.message.reply_text("You are not authorized to use this command.")

def help_command(update: Update, context: CallbackContext) -> None:
    """Displays a help message with available commands."""
    update.message.reply_text(
        "Available commands:\n"
        "/start - Start the bot\n"
        "/add (user_id) (duration_seconds) - Add a user with limited access\n"
        "/timeleft - Check remaining access time\n"
        "/processed - View processed domains\n"
        "/clear - Clear processed domains\n"
        "/list - List users and their access time\n"
        "/free (duration_in_seconds) - Grant free access to all users for specified duration\n"
        "/broadcast (message) - Broadcast a message to all users\n"
        "/active - Count the number of active users\n"
        "/help - Display this help message"
    )

def is_user_authorized(user_id: int) -> bool:
    """Checks if a user is authorized."""
    return user_id in ADMIN_USER_IDS or user_id in REGULAR_USER_IDS or (user_id in user_timers and time.time() < user_timers[user_id]) or global_free_access > time.time()

def main() -> None:
    """Main function to run the bot."""
    load_state()  # Load the state on startup

    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("`TELEGRAM_BOT_TOKEN` is not set. Please set it at the top of the script.")

    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("add", add_user))
    dispatcher.add_handler(CommandHandler("timeleft", time_left))
    dispatcher.add_handler(CommandHandler("processed", view_processed_domains))
    dispatcher.add_handler(CommandHandler("clear", clear_processed_domains))
    dispatcher.add_handler(CommandHandler("list", list_users))
    dispatcher.add_handler(CommandHandler("free", free_access))
    dispatcher.add_handler(CommandHandler("broadcast", broadcast))
    dispatcher.add_handler(CommandHandler("active", active_users))
    dispatcher.add_handler(CommandHandler("help", help_command))
    dispatcher.add_handler(MessageHandler(Filters.document, handle_document))
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_text))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
