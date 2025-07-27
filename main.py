import os
import asyncio
import logging
import tempfile
import shutil
from urllib.parse import urlparse
import re
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import yt_dlp
import requests

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Bot configuration from environment variables
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
DOWNLOAD_DIR = os.getenv('DOWNLOAD_DIR', './downloads')
MAX_FILE_SIZE = int(os.getenv('MAX_FILE_SIZE', '50')) * 1024 * 1024  # Convert MB to bytes

# Channel/Group settings for forced subscription
REQUIRED_CHANNELS = os.getenv('REQUIRED_CHANNELS', '').split(',') if os.getenv('REQUIRED_CHANNELS') else []

# Validate bot token
if not BOT_TOKEN:
    logger.error("❌ TELEGRAM_BOT_TOKEN not found in environment variables!")
    logger.error("💡 Create a .env file with your bot token:")
    logger.error("   TELEGRAM_BOT_TOKEN=your_bot_token_here")
    exit(1)

# Ensure download directory exists
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

class MediaDownloaderBot:
    def __init__(self):
        self.supported_platforms = {
            'youtube.com': 'YouTube',
            'youtu.be': 'YouTube',
            'twitter.com': 'Twitter/X',
            'x.com': 'Twitter/X',
            'instagram.com': 'Instagram',
            'soundcloud.com': 'SoundCloud',
            'tiktok.com': 'TikTok',
            'facebook.com': 'Facebook',
            'vimeo.com': 'Vimeo'
        }
        # Store URLs temporarily with short IDs
        self.url_cache = {}
    
    def is_supported_url(self, url):
        """Check if the URL is from a supported platform"""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            # Remove 'www.' prefix if present
            if domain.startswith('www.'):
                domain = domain[4:]
            
            is_supported = any(platform in domain for platform in self.supported_platforms.keys())
            return is_supported
        except Exception as e:
            logger.error(f"Error parsing URL {url}: {e}")
            return False
    
    def store_url(self, url, user_id):
        """Store URL with a short ID for callback data"""
        import hashlib
        import time
        
        # Create a short unique ID
        url_hash = hashlib.md5(f"{url}{user_id}{time.time()}".encode()).hexdigest()[:8]
        self.url_cache[url_hash] = url
        
        # Clean old entries (keep only last 100)
        if len(self.url_cache) > 100:
            old_keys = list(self.url_cache.keys())[:-50]
            for key in old_keys:
                del self.url_cache[key]
        
        return url_hash
    
    def get_url(self, url_id):
        """Retrieve URL from short ID"""
        return self.url_cache.get(url_id)
    
    def get_platform_name(self, url):
        """Get the platform name from URL"""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            if domain.startswith('www.'):
                domain = domain[4:]
            
            for platform, name in self.supported_platforms.items():
                if platform in domain:
                    return name
            return "Unknown"
        except:
            return "Unknown"
    
    async def download_media(self, url, format_type='best'):
        """Download media using yt-dlp with enhanced support for all platforms"""
        temp_dir = tempfile.mkdtemp(dir=DOWNLOAD_DIR)
        
        try:
            # Base yt-dlp options
            base_opts = {
                'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                'format': format_type,
                'noplaylist': True,
                'extractaudio': format_type == 'audio',
                'audioformat': 'mp3' if format_type == 'audio' else None,
                'audioquality': '192' if format_type == 'audio' else None,
                'retries': 3,
                'fragment_retries': 3,
                'skip_unavailable_fragments': True,
                'abort_on_unavailable_fragment': False,
                'writesubtitles': False,
                'writeautomaticsub': False,
                'ignoreerrors': False,
            }
            
            # Platform-specific strategies
            strategies = []
            
            if 'youtube.com' in url or 'youtu.be' in url:
                # YouTube Strategy 1: Cookie-based authentication with enhanced headers
                strategy1 = base_opts.copy()
                strategy1.update({
                    'format': 'best[height<=720]/best' if format_type == 'video' else 'bestaudio[ext=m4a]/bestaudio/best',
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                        'Accept-Language': 'en-us,en;q=0.5',
                        'Accept-Encoding': 'gzip,deflate',
                        'DNT': '1',
                        'Connection': 'keep-alive',
                        'Upgrade-Insecure-Requests': '1',
                    },
                    'extractor_args': {
                        'youtube': {
                            'skip': ['hls'],
                            'player_skip': ['configs'],
                            'player_client': ['android', 'web'],
                        }
                    }
                })
                
                # YouTube Strategy 2: Android client simulation
                strategy2 = base_opts.copy()
                strategy2.update({
                    'format': 'best[height<=480]/best' if format_type == 'video' else 'bestaudio/best',
                    'http_headers': {
                        'User-Agent': 'com.google.android.youtube/17.36.4 (Linux; U; Android 12; GB) gzip',
                        'X-YouTube-Client-Name': '3',
                        'X-YouTube-Client-Version': '17.36.4',
                    },
                    'extractor_args': {
                        'youtube': {
                            'player_client': ['android'],
                            'skip': ['dash', 'hls'],
                        }
                    }
                })
                
                # YouTube Strategy 3: iOS client simulation
                strategy3 = base_opts.copy()
                strategy3.update({
                    'format': 'best[height<=480]/best' if format_type == 'video' else 'bestaudio/best',
                    'http_headers': {
                        'User-Agent': 'com.google.ios.youtube/17.36.4 (iPhone14,3; U; CPU iOS 15_6 like Mac OS X)',
                        'X-YouTube-Client-Name': '5',
                        'X-YouTube-Client-Version': '17.36.4',
                    },
                    'extractor_args': {
                        'youtube': {
                            'player_client': ['ios'],
                            'skip': ['dash'],
                        }
                    }
                })
                
                # YouTube Strategy 4: Googlebot simulation
                strategy4 = base_opts.copy()
                strategy4.update({
                    'format': 'best[height<=720]/best' if format_type == 'video' else 'bestaudio/best',
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
                        'Accept': '*/*',
                    },
                    'extractor_args': {
                        'youtube': {
                            'player_client': ['web'],
                            'skip': ['hls'],
                        }
                    },
                    'age_limit': 999,
                })
                
                # YouTube Strategy 5: Alternative extraction with delays
                strategy5 = base_opts.copy()
                strategy5.update({
                    'format': 'worst[height>=240]/worst' if format_type == 'video' else 'worst',
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                        'Accept-Language': 'en-US,en;q=0.5',
                        'Cache-Control': 'no-cache',
                        'Pragma': 'no-cache',
                    },
                    'extractor_args': {
                        'youtube': {
                            'player_client': ['web', 'android'],
                            'player_skip': ['webpage'],
                        }
                    },
                    'sleep_interval': 1,
                    'max_sleep_interval': 3,
                })
                
                # Add cookies to all YouTube strategies if available
                cookies_file = os.getenv('YTDLP_COOKIES')
                cookies_browser = os.getenv('YTDLP_COOKIES_BROWSER')
                
                if cookies_file and os.path.exists(cookies_file):
                    for strategy in [strategy1, strategy2, strategy3, strategy4, strategy5]:
                        strategy['cookiefile'] = cookies_file
                    logger.info(f"✅ Using cookies from file: {cookies_file}")
                elif cookies_browser:
                    for strategy in [strategy1, strategy2, strategy3, strategy4, strategy5]:
                        strategy['cookiesfrombrowser'] = (cookies_browser,)
                    logger.info(f"✅ Using cookies from {cookies_browser} browser")
                else:
                    logger.warning("⚠️ No cookies configured - YouTube downloads may fail")
                
                strategies.extend([strategy1, strategy2, strategy3, strategy4, strategy5])
                
            elif 'twitter.com' in url or 'x.com' in url:
                # Twitter Strategy 1: Legacy API
                strategy1 = base_opts.copy()
                strategy1.update({
                    'format': 'best[height<=720]/best',
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                        'Accept-Language': 'en-US,en;q=0.9',
                        'Accept-Encoding': 'gzip, deflate, br',
                        'DNT': '1',
                        'Connection': 'keep-alive',
                        'Upgrade-Insecure-Requests': '1',
                    },
                    'extractor_args': {
                        'twitter': {
                            'api': 'legacy',
                        }
                    }
                })
                strategies.append(strategy1)
                
                # Twitter Strategy 2: Syndication API
                strategy2 = base_opts.copy()
                strategy2.update({
                    'format': 'best[height<=480]/best',
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
                        'Accept': '*/*',
                        'Accept-Language': 'en-US,en;q=0.5',
                    },
                    'extractor_args': {
                        'twitter': {
                            'api': 'syndication',
                        }
                    }
                })
                strategies.append(strategy2)
                
            elif 'instagram.com' in url:
                # Instagram Strategy 1: Mobile browser simulation
                strategy1 = base_opts.copy()
                strategy1.update({
                    'format': 'best[height<=1080]',
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                        'Accept-Language': 'en-US,en;q=0.9',
                        'Accept-Encoding': 'gzip, deflate, br',
                        'DNT': '1',
                        'Connection': 'keep-alive',
                        'Upgrade-Insecure-Requests': '1',
                    },
                    'sleep_interval': 2,
                    'max_sleep_interval': 8,
                })
                strategies.append(strategy1)
                
                # Instagram Strategy 2: Android app simulation
                strategy2 = base_opts.copy()
                strategy2.update({
                    'format': 'best',
                    'http_headers': {
                        'User-Agent': 'Instagram 302.0.0.23.113 Android (33/13; 420dpi; 1080x2340; samsung; SM-G991B; o1s; exynos2100; en_US; 314665256)',
                        'X-IG-App-ID': '124024574287414',
                    },
                    'sleep_interval': 3,
                })
                strategies.append(strategy2)
                
            else:
                # Default strategy for other platforms
                strategy = base_opts.copy()
                strategy.update({
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    }
                })
                
                # Platform-specific format adjustments
                if 'soundcloud.com' in url:
                    strategy['format'] = 'bestaudio/best'
                    if format_type == 'audio':
                        strategy['postprocessors'] = [{
                            'key': 'FFmpegExtractAudio',
                            'preferredcodec': 'mp3',
                            'preferredquality': '192',
                        }]
                elif 'tiktok.com' in url:
                    strategy['format'] = 'best[height<=720]'
                
                strategies.append(strategy)
            
            # Try each strategy
            last_error = None
            for i, strategy in enumerate(strategies):
                try:
                    logger.info(f"Trying download strategy {i+1}/{len(strategies)}")
                    
                    # Handle URL conversion for Twitter
                    current_url = url
                    if 'x.com' in url and 'twitter.com' not in url:
                        current_url = url.replace('x.com', 'twitter.com')
                    
                    with yt_dlp.YoutubeDL(strategy) as ydl:
                        # Extract info first
                        try:
                            info = ydl.extract_info(current_url, download=False)
                        except Exception as e:
                            error_msg = str(e).lower()
                            logger.warning(f"Strategy {i+1} failed during info extraction: {e}")
                            
                            # Handle specific errors
                            if any(keyword in error_msg for keyword in ['sign in', 'bot', 'automated']):
                                if i < len(strategies) - 1:
                                    logger.info(f"Bot detection on strategy {i+1}, trying next strategy...")
                                    continue
                                return None, "❌ Platform detected automated access. Try a different video or wait 10-15 minutes."
                            elif any(keyword in error_msg for keyword in ['private', 'unavailable', 'not found']):
                                if i < len(strategies) - 1:
                                    continue
                                return None, "❌ This content is private, unavailable, or was removed."
                            elif any(keyword in error_msg for keyword in ['age', 'restricted']):
                                return None, "❌ This content is age-restricted and cannot be downloaded."
                            elif any(keyword in error_msg for keyword in ['login required', 'rate-limit', 'rate limit']):
                                if i < len(strategies) - 1:
                                    continue
                                return None, "❌ Login required or rate-limited. This content may be private or restricted."
                            else:
                                if i < len(strategies) - 1:
                                    continue
                                return None, f"❌ Error accessing content: {str(e)[:100]}..."
                        
                        title = info.get('title', 'Unknown')
                        duration = info.get('duration', 0)
                        
                        # Check file size
                        filesize = info.get('filesize') or info.get('filesize_approx', 0)
                        max_size_mb = MAX_FILE_SIZE // (1024 * 1024)
                        if filesize and filesize > MAX_FILE_SIZE:
                            return None, f"❌ File too large ({filesize // (1024*1024)}MB). Telegram limit is {max_size_mb}MB."
                        
                        # Download the media
                        try:
                            ydl.download([current_url])
                            
                            # Find downloaded file
                            files = os.listdir(temp_dir)
                            if files:
                                filepath = os.path.join(temp_dir, files[0])
                                logger.info(f"✅ Successfully downloaded with strategy {i+1}")
                                return filepath, None
                            else:
                                if i < len(strategies) - 1:
                                    continue
                                return None, "❌ Download failed - no file created"
                        
                        except Exception as e:
                            error_msg = str(e).lower()
                            last_error = str(e)
                            logger.warning(f"Strategy {i+1} failed during download: {e}")
                            
                            if any(keyword in error_msg for keyword in ['sign in', 'bot', 'automated']):
                                if i < len(strategies) - 1:
                                    continue
                                return None, "❌ Platform detected automated access. Try again later."
                            elif 'http error 403' in error_msg:
                                if i < len(strategies) - 1:
                                    continue
                                return None, "❌ Access forbidden. Content may be region-restricted."
                            elif 'http error 404' in error_msg:
                                return None, "❌ Content not found. The link may be broken or removed."
                            else:
                                if i < len(strategies) - 1:
                                    continue
                                return None, f"❌ Download failed: {str(e)[:100]}..."
                
                except Exception as e:
                    last_error = str(e)
                    logger.warning(f"Strategy {i+1} failed with exception: {e}")
                    if i < len(strategies) - 1:
                        continue
                    break
            
            # All strategies failed
            if last_error:
                error_msg = str(last_error).lower()
                if any(keyword in error_msg for keyword in ['sign in', 'bot', 'automated']):
                    return None, "❌ Platform detected automated access. All bypass attempts failed."
                else:
                    return None, f"❌ All download strategies failed. Last error: {str(last_error)[:100]}..."
            else:
                return None, "❌ All download strategies failed for unknown reasons."
        
        except Exception as e:
            logger.error(f"Unexpected error in download_media: {e}")
            return None, f"❌ Unexpected error: {str(e)[:100]}..."
        
        finally:
            # Cleanup happens in the calling function
            pass

# Initialize bot
bot = MediaDownloaderBot()

async def check_user_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> tuple[bool, list]:
    """Check if user is member of required channels/groups"""
    if not REQUIRED_CHANNELS:
        return True, []  # No channels required
    
    not_joined = []
    
    for channel in REQUIRED_CHANNELS:
        channel = channel.strip()
        if not channel:
            continue
            
        try:
            member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status in ['left', 'kicked']:
                not_joined.append(channel)
        except Exception as e:
            logger.warning(f"Could not check membership for {channel}: {e}")
            # If we can't check, assume not joined for security
            not_joined.append(channel)
    
    return len(not_joined) == 0, not_joined

async def create_join_keyboard(not_joined_channels: list) -> InlineKeyboardMarkup:
    """Create keyboard with join buttons for required channels"""
    keyboard = []
    
    for channel in not_joined_channels:
        # Create join button for each channel
        if channel.startswith('@'):
            channel_name = channel[1:]  # Remove @ symbol
            url = f"https://t.me/{channel_name}"
        elif channel.startswith('-100'):
            # For channel IDs, you need to provide the channel username manually
            url = f"https://t.me/joinchat/{channel[4:]}"  # This might not work for all cases
        else:
            url = f"https://t.me/{channel}"
        
        keyboard.append([InlineKeyboardButton(f"📢 Join {channel}", url=url)])
    
    # Add check membership button
    keyboard.append([InlineKeyboardButton("✅ Check Membership", callback_data="check_membership")])
    
    return InlineKeyboardMarkup(keyboard)

async def membership_required_message(not_joined_channels: list) -> str:
    """Create membership required message"""
    message = """
🔒 **Membership Required**

To use this bot, you must join our channel(s):

"""
    
    for channel in not_joined_channels:
        message += f"📢 {channel}\n"
    
    message += """
**Steps:**
1. Click the join button(s) below
2. Join the channel(s)
3. Click "✅ Check Membership"
4. Start using the bot!

This helps support our community 🙏
    """
    
    return message

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    user_id = update.message.from_user.id
    
    # Check if user is member of required channels
    is_member, not_joined = await check_user_membership(user_id, context)
    
    if not is_member:
        # User needs to join channels first
        message = await membership_required_message(not_joined)
        keyboard = await create_join_keyboard(not_joined)
        
        await update.message.reply_text(message, reply_markup=keyboard, parse_mode='HTML')
        return
    
    # User is member, show welcome message
    max_size_mb = MAX_FILE_SIZE // (1024*1024)
    welcome_text = f"""
🎬 <b>Media Downloader Bot</b>

Send me a URL from any of these platforms and I'll download it for you:

📱 <b>Supported Platforms:</b>
• YouTube (videos & audio)
• Twitter/X (videos)
• Instagram (posts & stories)
• TikTok (videos)
• SoundCloud (audio)
• Facebook (videos)
• Vimeo (videos)

📝 <b>How to use:</b>
1. Send me a URL
2. Choose video or audio format
3. I'll download and send it to you!

⚠️ <b>Note:</b> Files must be under {max_size_mb}MB due to Telegram limits.
    """
    
    await update.message.reply_text(welcome_text, parse_mode='HTML')

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle URL messages"""
    user_id = update.message.from_user.id
    
    # Check if user is member of required channels
    is_member, not_joined = await check_user_membership(user_id, context)
    
    if not is_member:
        # User needs to join channels first
        message = await membership_required_message(not_joined)
        keyboard = await create_join_keyboard(not_joined)
        
        await update.message.reply_text(message, reply_markup=keyboard, parse_mode='HTML')
        return
    
    original_text = update.message.text.strip()
    
    # Remove bot mention if present (for group chats)
    text = original_text
    if update.message.chat.type in ['group', 'supergroup']:
        # Remove @botname mentions
        import re
        text = re.sub(r'@\w+', '', text).strip()
    
    # Extract URL from text
    url_pattern = r'https?://[^\s]+'
    urls = re.findall(url_pattern, text)
    
    if urls:
        url = urls[0]  # Take the first URL found
    else:
        # If no URL found in cleaned text, try original text
        urls = re.findall(url_pattern, original_text)
        if urls:
            url = urls[0]
        else:
            # No URL found at all - ignore this message silently
            # Only respond if it looks like they're trying to send a URL
            if any(word in text.lower() for word in ['http', 'www.', '.com', '.org', '.net', 'youtube', 'instagram', 'soundcloud', 'twitter', 'tiktok']):
                await update.message.reply_text("❌ Please send a valid URL starting with http:// or https://")
            return
    
    logger.info(f"Received URL: {url}")
    
    # Check if platform is supported
    if not bot.is_supported_url(url):
        supported = ", ".join(bot.supported_platforms.values())
        logger.warning(f"Unsupported URL: {url}")
        
        # Get domain for display
        try:
            domain = urlparse(url).netloc
        except:
            domain = "unknown"
        
        await update.message.reply_text(
            f"❌ Platform not supported.\n\n"
            f"Supported platforms: {supported}\n\n"
            f"Your URL domain: {domain}",
            parse_mode=None
        )
        return
    
    platform = bot.get_platform_name(url)
    logger.info(f"Detected platform: {platform}")
    
    # Store URL with short ID for callback data
    url_id = bot.store_url(url, user_id)
    
    # For SoundCloud, only show audio option
    if 'soundcloud.com' in url.lower():
        keyboard = [
            [InlineKeyboardButton("🎵 Download Audio", callback_data=f"audio|{url_id}")]
        ]
    else:
        # Create format selection keyboard for other platforms
        keyboard = [
            [
                InlineKeyboardButton("🎥 Video", callback_data=f"video|{url_id}"),
                InlineKeyboardButton("🎵 Audio", callback_data=f"audio|{url_id}")
            ]
        ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"📱 Detected: {platform}\n\nChoose download format:",
        reply_markup=reply_markup
    )

async def handle_membership_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle membership check callback"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    # Check if user is now member of required channels
    is_member, not_joined = await check_user_membership(user_id, context)
    
    if is_member:
        # User has joined all channels
        max_size_mb = MAX_FILE_SIZE // (1024*1024)
        welcome_text = f"""
✅ <b>Welcome! You're now verified!</b>

🎬 <b>Media Downloader Bot</b>

Send me a URL from any of these platforms and I'll download it for you:

📱 <b>Supported Platforms:</b>
• YouTube (videos & audio)
• Twitter/X (videos)  
• Instagram (posts & stories)
• TikTok (videos)
• SoundCloud (audio)
• Facebook (videos)
• Vimeo (videos)

📝 <b>How to use:</b>
1. Send me a URL
2. Choose video or audio format  
3. I'll download and send it to you!

⚠️ <b>Note:</b> Files must be under {max_size_mb}MB due to Telegram limits.
        """
        
        await query.edit_message_text(welcome_text, parse_mode='HTML')
    else:
        # User still hasn't joined all channels
        message = await membership_required_message(not_joined)
        keyboard = await create_join_keyboard(not_joined)
        
        await query.edit_message_text(
            message + "\n\n❌ <b>You still need to join the channels above!</b>",
            reply_markup=keyboard,
            parse_mode='HTML'
        )

async def handle_format_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle format selection callback"""
    query = update.callback_query
    await query.answer()
    
    # Handle membership check
    if query.data == "check_membership":
        await handle_membership_check(update, context)
        return
    
    user_id = query.from_user.id
    
    # Check if user is member of required channels
    is_member, not_joined = await check_user_membership(user_id, context)
    
    if not is_member:
        # User needs to join channels first
        message = await membership_required_message(not_joined)
        keyboard = await create_join_keyboard(not_joined)
        
        await query.edit_message_text(message, reply_markup=keyboard, parse_mode='HTML')
        return
    
    data_parts = query.data.split('|', 1)
    if len(data_parts) != 2:
        await query.edit_message_text("❌ Invalid selection")
        return
    
    format_type, url_id = data_parts
    
    # Retrieve the actual URL
    url = bot.get_url(url_id)
    if not url:
        await query.edit_message_text("❌ Session expired. Please send the URL again.")
        return
    
    platform = bot.get_platform_name(url)
    
    # Show downloading message
    format_emoji = "🎥" if format_type == "video" else "🎵"
    await query.edit_message_text(
        f"{format_emoji} Downloading {format_type} from {platform}...\n\n"
        f"⏳ This may take a moment depending on file size."
    )
    
    # Download the media
    try:
        filepath, error = await bot.download_media(url, format_type)
        
        if error:
            error_text = f"❌ {error}"
            
            # Add helpful suggestions for common errors
            if "YouTube detected automated access" in error or "bot detection" in error.lower():
                error_text += "\n\n💡 Suggestions:\n"
                error_text += "• Try a different YouTube video\n"
                error_text += "• Wait 10-15 minutes before trying again\n"
                error_text += "• Use shorter videos (under 5 minutes)\n"
                error_text += "• Educational content works better"
            elif "private" in error.lower() or "unavailable" in error.lower():
                error_text += "\n\n💡 Try: Make sure the video is public and available in your region"
            elif "age-restricted" in error.lower():
                error_text += "\n\n💡 Note: Age-restricted content cannot be downloaded"
            
            await query.edit_message_text(error_text)
            return
        
        if not filepath or not os.path.exists(filepath):
            await query.edit_message_text("❌ Download failed - file not found")
            return
        
        # Get file info
        file_size = os.path.getsize(filepath)
        max_size_mb = MAX_FILE_SIZE // (1024 * 1024)
        if file_size > MAX_FILE_SIZE:
            await query.edit_message_text(f"❌ File too large ({file_size // (1024*1024)}MB). Telegram limit is {max_size_mb}MB.")
            return
        
        # Send the file
        await query.edit_message_text(f"📤 Uploading {format_type}...")
        
        with open(filepath, 'rb') as file:
            if format_type == 'audio':
                await context.bot.send_audio(
                    chat_id=query.message.chat_id,
                    audio=file,
                    caption=f"🎵 Downloaded from {platform}"
                )
            else:
                await context.bot.send_video(
                    chat_id=query.message.chat_id,
                    video=file,
                    caption=f"🎥 Downloaded from {platform}",
                    supports_streaming=True
                )
        
        await query.edit_message_text(f"✅ {format_type.title()} downloaded successfully!")
        
    except Exception as e:
        logger.error(f"Error in download process: {e}")
        await query.edit_message_text(f"❌ An error occurred: {str(e)}")

    finally:
        # Clean up temporary files
        try:
            if 'filepath' in locals() and filepath and os.path.exists(filepath):
                # Get temp directory from filepath
                temp_dir = os.path.dirname(filepath)

                os.remove(filepath)
                logger.info(f"🗑️ Deleted file: {os.path.basename(filepath)}")

                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    logger.info(f"🗑️ Deleted temp directory: {os.path.basename(temp_dir)}")

        except Exception as e:
            logger.warning(f"⚠️ Cleanup failed: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /help is issued."""
    max_size_mb = MAX_FILE_SIZE // (1024*1024)
    help_text = f"""
🆘 Help - Media Downloader Bot

📝 How to download:
1. Copy a URL from supported platforms
2. Send it to this bot
3. Choose video or audio format
4. Wait for download and upload

📱 Supported Platforms:
• YouTube - Videos and audio
• Twitter/X - Videos and GIFs
• Instagram - Posts and stories
• TikTok - Videos
• SoundCloud - Audio tracks
• Facebook - Videos
• Vimeo - Videos

⚠️ Limitations:
• Maximum file size: {max_size_mb}MB
• Audio is converted to MP3 format
• Some private or age-restricted content may not work

💡 Tips:
• For YouTube, you can use both youtube.com and youtu.be links
• Instagram stories require the full URL
• Some platforms may have regional restrictions
• Educational content works better than viral videos
    """
    
    await update.message.reply_text(help_text)

def main():
    """Start the bot."""
    if not BOT_TOKEN:
        logger.error("❌ Bot token not configured!")
        return
    
    logger.info("🤖 Initializing Telegram Media Downloader Bot...")
    logger.info(f"📁 Download directory: {DOWNLOAD_DIR}")
    logger.info(f"📏 Max file size: {MAX_FILE_SIZE // (1024*1024)}MB")
    
    # Check cookie configuration
    cookies_file = os.getenv('YTDLP_COOKIES')
    if cookies_file and os.path.exists(cookies_file):
        logger.info(f"🍪 YouTube cookies configured: {cookies_file}")
    else:
        logger.warning("⚠️ No YouTube cookies configured - downloads may fail")
    
    # Create the Application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    application.add_handler(CallbackQueryHandler(handle_format_selection))
    
    # Run the bot until the user presses Ctrl-C
    logger.info("🚀 Bot is running! Send /start to begin.")
    logger.info("📱 Ready to download media from supported platforms!")
    
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Bot crashed: {e}")
        raise

if __name__ == '__main__':
    main()