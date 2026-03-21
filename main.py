import asyncio
import logging
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup, default_state
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

import database as db

# --- CONFIGURATION ---
API_TOKEN = '8410551309:AAHsMNd8oPLEkOM7ELF90PnIzigEIlamTaM'
ADMIN_ID = 670167033
REQUIRED_CHANNELS = [-1002984894665] # teshik_iqtisod
SYNC_CHANNEL_ID = -1003379413807 # Keep your sync channel or change as needed

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

class AdminStates(StatesGroup):
    waiting_for_movie_code = State()
    waiting_for_movie_video = State()

# --- MIDDLEWARE / CLEAN FUNCTION FOR SUB CHECKS ---
async def check_subscription(user_id: int) -> bool:
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status in ['left', 'kicked']:
                return False
        except TelegramBadRequest:
            logging.error(f"Bot is not an admin in {channel} or channel doesn't exist.")
            return False
        except TelegramForbiddenError:
            logging.error(f"Bot was blocked by the user or kicked from {channel}.")
            return False
    return True

def get_subscription_keyboard():
    keyboard = [
        [InlineKeyboardButton(text="📢 Kanalga a'zo bo'lish", url="https://t.me/teshik_iqtisod")],
        [InlineKeyboardButton(text="✅ Tasdiqlash", callback_data="check_sub")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_admin_keyboard():
    keyboard = [
        [InlineKeyboardButton(text="📥 Kino yuklash", callback_data="admin_upload_movie")],
        [InlineKeyboardButton(text="🎬 Kinolar ro'yxati", callback_data="admin_movies_list")],
        [InlineKeyboardButton(text="🔄 Bazani yangilash (Sync)", callback_data="admin_sync")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Reklama yuborish", callback_data="admin_broadcast")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# --- USER LOGIC ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    # Track user growth
    await db.add_user(message.from_user.id)
    
    if message.from_user.id == ADMIN_ID:
        await message.answer(
            "Xush kelibsiz, Admin! Boshqaruv paneliga xush kelibsiz. Qanday amal bajaramiz?",
            reply_markup=get_admin_keyboard()
        )
    else:
        await message.answer(
            "Xush kelibsiz! Kino kodini yuboring va men sizga videoni topib beraman.\n\n"
            "Botdan foydalanish uchun quyidagi kanalga a'zo bo'lishingiz shart:",
            reply_markup=get_subscription_keyboard()
        )

@dp.callback_query(F.data == "check_sub")
async def cb_check_sub(callback: types.CallbackQuery, state: FSMContext):
    is_subscribed = await check_subscription(callback.from_user.id)
    if is_subscribed:
        await state.clear()
        await callback.message.answer("Rahmat! Endi kino kodini yuborishingiz mumkin.")
        await callback.message.delete()
    else:
        await callback.answer("Siz hali kanalga a'zo bo'lmadingiz!", show_alert=True)

# Process any numeric message as a movie code (only if no state is active)
@dp.message(F.text.regexp(r'^\d+$'), default_state)
async def process_movie_code(message: types.Message):
    user_id = message.from_user.id
    
    is_subscribed = await check_subscription(user_id)
    if not is_subscribed:
        await message.answer(
            "Botdan foydalanish uchun quyidagi kanalga a'zo bo'lishingiz shart:",
            reply_markup=get_subscription_keyboard()
        )
        return

    movie_code = message.text.strip()
    movie = await db.get_movie(movie_code)
    
    if movie:
        file_id, caption = movie
        try:
            # Send the video alone with its originally stored caption.
            await message.answer_video(video=file_id, caption=caption)
        except Exception as e:
            logging.error(f"Error sending video: {e}")
            await message.answer("Videoni yuborishda xatolik yuz berdi. U cheklangan yoki o'chirilgan bo'lishi mumkin.")
    else:
        await message.answer("Ushbu kod bilan kino topilmadi.")

# --- ADMIN COMMANDS & CHANNEL SCANNER ---
@dp.message(Command("sync"))
async def cmd_sync(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    # The hacky workaround using exactly Bot API is to incrementingly try to forward messages by their IDs.
    
    await message.answer("Kanal skaner qilinmoqda, kuting... (Barcha oldingi xabarlarni tekshirish)")
    
    # We will blind-scan message IDs backwards from an estimated recent ID down to 1.
    # We obtain the current message ID by sending a temp message to the channel and deleting it.
    try:
        temp_msg = await bot.send_message(chat_id=SYNC_CHANNEL_ID, text="sync_init")
        latest_msg_id = temp_msg.message_id
        await temp_msg.delete()
    except Exception as e:
        await message.answer(f"Sinxronizatsiyani ishga tushirib bo'lmadi. Bot kanalda admin ekanligiga ishonchingiz komilmi? Xato: {e}")
        return

    start_id = 1 # Scan all the way down to the first message
    
    success_count = 0
    await message.answer(f"Scanning from message ID {latest_msg_id} down to {start_id}...")
    
    for msg_id in range(latest_msg_id, start_id - 1, -1):
        try:
            # Forward the message to the ADMIN to read its content and obtain file_id
            fwd_msg = await bot.forward_message(chat_id=ADMIN_ID, from_chat_id=SYNC_CHANNEL_ID, message_id=msg_id)
            
            if fwd_msg.video and fwd_msg.caption:
                caption = fwd_msg.caption.strip()
                if caption.isdigit():
                    movie_code = caption
                    file_id = fwd_msg.video.file_id
                    await db.save_movie(movie_code, file_id, caption)
                    success_count += 1
            
            # Delete the forwarded message so it doesn't spam the admin chat
            await fwd_msg.delete()
            
            # Simple rate limiting protection
            await asyncio.sleep(0.5)
            
        except TelegramBadRequest:
            # Message doesn't exist, was deleted, or can't be forwarded
            pass
        except TelegramForbiddenError:
            # Bot blocked
            break
        except Exception as e:
            logging.error(f"Error syncing msg {msg_id}: {e}")
            await asyncio.sleep(1)
            
    await message.answer(f"Sinxronizatsiya yakunlandi! {success_count} ta yangi kino qo'shildi.")

@dp.callback_query(F.data == "admin_sync")
async def cb_admin_sync(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Bu funksiya faqat adminlar uchun!", show_alert=True)
        return
    
    # Simple reply indicating the start of the synchronization process using a callback button
    await callback.message.answer("Sinxronizatsiya jarayoni boshlanmoqda... Iltimos /sync buyrug'idan foydalaning yoxud biroz sozlashimiz kerak!")
    await callback.answer()

@dp.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: types.CallbackQuery):
    if callback.fromuser.id != ADMIN_ID:
        await callback.answer("Bu funksiya faqat adminlar uchun!", show_alert=True)
        return
        
    users_count, movies_count = await db.get_stats()
    text = (
        f"📊 **Statistika**\n\n"
        f"👥 Umumiy foydalanuvchilar: {users_count} ta\n"
        f"🎬 Umumiy kinolar: {movies_count} ta"
    )
    await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "admin_movies_list")
async def cb_admin_movies_list(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Bu funksiya faqat adminlar uchun!", show_alert=True)
        return
        
    movies = await db.get_all_movies()
    if not movies:
        await callback.message.answer("Bazada hozircha kinolar yo'q.")
        await callback.answer()
        return

    text = f"🎬 **Kinolar ro'yxati ({len(movies)} ta)**\n\n"
    text += ", ".join(movies[:100]) # Displaying max 100 codes to avoid long messages error
    if len(movies) > 100:
        text += f"\n... va yana {len(movies) - 100} ta qolgan."
        
    await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "admin_upload_movie")
async def cb_admin_upload_movie(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Bu funksiya faqat adminlar uchun!", show_alert=True)
        return
    
    await callback.message.answer("Videoni to'g'ridan-to'g'ri menga yuboring:")
    await state.set_state(AdminStates.waiting_for_movie_video)
    await callback.answer()

@dp.message(F.video, AdminStates.waiting_for_movie_video)
async def process_admin_video(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    caption = message.caption if message.caption else ""
    # Save BOTH the file_id and the original full description temporarily
    await state.update_data(file_id=message.video.file_id, caption=caption)
    await message.answer("Ushbu kino uchun kod kiriting:")
    await state.set_state(AdminStates.waiting_for_movie_code)

@dp.message(AdminStates.waiting_for_movie_code, F.text.regexp(r'^\d+$'))
async def process_admin_movie_code(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
        
    data = await state.get_data()
    file_id = data.get('file_id')
    caption = data.get('caption', "")
    movie_code = message.text.strip()
    
    # Save the movie with the fully preserved caption
    await db.save_movie(movie_code, file_id, caption)
    await message.answer(f"Kino muvaffaqiyatli saqlandi! Kodi: {movie_code}")
    await state.clear()

@dp.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Bu funksiya faqat adminlar uchun!", show_alert=True)
        return
    await callback.answer("Reklama yuborish funksiyasi tez orada qo'shiladi!", show_alert=True)

# Alternatively, wait for new channel posts to automatically sync
@dp.channel_post()
async def new_channel_post(message: types.Message):
    if message.chat.id == SYNC_CHANNEL_ID:
        if message.video and message.caption:
            caption = message.caption.strip()
            if caption.isdigit():
                movie_code = caption
                file_id = message.video.file_id
                await db.save_movie(movie_code, file_id, caption)

async def main():
    # Initialize the sqlite database and tables
    await db.init_db()
    
    # Drop pending updates and begin polling
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
