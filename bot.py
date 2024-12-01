from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
import json
import os
from utils import VideoDownloader
import jiocine
import logging
import xmltodict

# Basic logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Load config
with open('config.json', 'r') as f:
    config = json.load(f)

# Initialize
app = Client("jiocinema_bot", api_id=config['api_id'], api_hash=config['api_hash'], bot_token=config['bot_token'])
downloader = VideoDownloader()
user_data = {}

@app.on_message(filters.command("start"))
async def start_command(_, message):
    await message.reply_text("Send me a JioCinema URL using /dl command\nExample: /dl https://www.jiocinema.com/movies/xyz")

@app.on_message(filters.command("dl"))
async def download_command(client, message):
    try:
        args = message.text.split()
        if len(args) < 2:
            await message.reply_text("Please provide a JioCinema URL!\nExample: /dl [URL]")
            return
        
        url = args[1]
        msg = await message.reply_text("🔍 Processing URL...")

        if "jiocinema.com" not in url:
            await msg.edit_text("❌ Please provide a valid JioCinema URL!")
            return

        content_id = url.split('/')[-1]
        
        # Get content details
        content_data = jiocine.getContentDetails(content_id)
        if not content_data:
            await msg.edit_text("❌ Failed to get content details! Please check URL.")
            return

        # Get token and playback data
        token = jiocine.fetchGuestToken()
        if not token:
            await msg.edit_text("❌ Failed to get access token!")
            return

        content_playback = jiocine.fetchPlaybackData(content_id, token)
        if not content_playback:
            await msg.edit_text("❌ Failed to get playback data!")
            return

        # Store data
        user_id = message.from_user.id
        user_data[user_id] = {
            'content_data': content_data,
            'content_playback': content_playback,
            'token': token,
            'selected_audio': []
        }

        # Get MPD URL and parse
        mpd_url = None
        for url_data in content_playback.get('playbackUrls', []):
            if url_data.get('streamtype') == 'dash':
                mpd_url = url_data.get('url')
                break

        if not mpd_url:
            await msg.edit_text("❌ No valid playback URL found!")
            return

        mpd_data = jiocine.getMPDData(mpd_url)
        if not mpd_data or 'MPD' not in mpd_data:
            await msg.edit_text("❌ Failed to parse video data!")
            return

        # Parse qualities and audio tracks
        period = mpd_data['MPD']['Period']
        adaptation_sets = period.get('AdaptationSet', [])
        if not isinstance(adaptation_sets, list):
            adaptation_sets = [adaptation_sets]

        qualities = []
        audio_tracks = []

        for adaptation_set in adaptation_sets:
            if isinstance(adaptation_set, dict):
                mime_type = adaptation_set.get('@mimeType', '')
                
                if mime_type.startswith('video/'):
                    representations = adaptation_set.get('Representation', [])
                    if not isinstance(representations, list):
                        representations = [representations]
                    
                    for rep in representations:
                        height = rep.get('@height')
                        bandwidth = rep.get('@bandwidth')
                        if height and bandwidth:
                            bitrate_mbps = round(int(bandwidth) / 1000000, 1)
                            qualities.append({
                                'height': int(height),
                                'bitrate': bitrate_mbps
                            })
                
                elif mime_type.startswith('audio/'):
                    lang = adaptation_set.get('@lang')
                    if lang:
                        lang_name = jiocine.LANG_MAP.get(lang, lang)
                        audio_tracks.append({
                            'id': adaptation_set.get('@id', ''),
                            'language': lang_name,
                            'channels': int(adaptation_set.get('@audioChannelConfiguration', {}).get('@value', 2)),
                            'codec': adaptation_set.get('@codecs', 'AAC'),
                            'bitrate': round(int(adaptation_set.get('Representation', [{}])[0].get('@bandwidth', 0)) / 1000)
                        })

        # Sort qualities
        qualities.sort(key=lambda x: (x['height'], x['bitrate']), reverse=True)
        
        # Store audio tracks
        user_data[user_id]['audio_tracks'] = audio_tracks

        # Create quality selection buttons
        keyboard = []
        for quality in qualities:
            keyboard.append([
                InlineKeyboardButton(
                    text=f"{quality['height']}p ({quality['bitrate']} Mbps)",
                    callback_data=f"quality_{quality['height']}_{quality['bitrate']}"
                )
            ])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await msg.edit_text(
            f"🎬 {content_data.get('name', 'Video')}\n\n"
            "Select Video Quality:",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error: {str(e)}")
        await msg.edit_text("❌ An error occurred! Please try again.")

@app.on_callback_query()
async def button_callback(client, callback_query):
    try:
        user_id = callback_query.from_user.id
        data = callback_query.data.split('_')
        action = data[0]

        if action == "quality":
            height = data[1]
            bitrate = float(data[2])
            user_data[user_id]['quality'] = height
            user_data[user_id]['bitrate'] = bitrate
            
            # Show audio selection
            keyboard = []
            for track in user_data[user_id].get('audio_tracks', []):
                display_text = f"{track['language']} ({track['codec']}"
                if track['channels'] > 2:
                    display_text += f" {track['channels']}.1"
                else:
                    display_text += " 2.0"
                display_text += f" {track['bitrate']}kbps)"
                
                keyboard.append([
                    InlineKeyboardButton(
                        text=f"☐ {display_text}",
                        callback_data=f"audio_{track['id']}"
                    )
                ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await callback_query.message.edit_text(
                "Select Audio Track(s):\n"
                "You can select multiple audio tracks ✅",
                reply_markup=reply_markup
            )
        
        elif action == "audio":
            track_id = '_'.join(data[1:])
            if 'selected_audio' not in user_data[user_id]:
                user_data[user_id]['selected_audio'] = []
            
            # Toggle selection
            if track_id in user_data[user_id]['selected_audio']:
                user_data[user_id]['selected_audio'].remove(track_id)
            else:
                user_data[user_id]['selected_audio'].append(track_id)
            
            # Update audio selection menu
            keyboard = []
            for track in user_data[user_id].get('audio_tracks', []):
                is_selected = track['id'] in user_data[user_id]['selected_audio']
                display_text = f"{track['language']} ({track['codec']}"
                if track['channels'] > 2:
                    display_text += f" {track['channels']}.1"
                else:
                    display_text += " 2.0"
                display_text += f" {track['bitrate']}kbps)"
                
                keyboard.append([
                    InlineKeyboardButton(
                        text=f"✅ {display_text}" if is_selected else f"☐ {display_text}",
                        callback_data=f"audio_{track['id']}"
                    )
                ])
            
            if user_data[user_id]['selected_audio']:
                keyboard.append([
                    InlineKeyboardButton(
                        text=f"Start Download ({len(user_data[user_id]['selected_audio'])} Audio Tracks)",
                        callback_data="done"
                    )
                ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await callback_query.message.edit_text(
                "Select Audio Track(s):\n"
                "You can select multiple audio tracks ✅",
                reply_markup=reply_markup
            )
        
        elif action == "done":
            if not user_data[user_id].get('selected_audio'):
                await callback_query.answer("Please select at least one audio track!", show_alert=True)
                return
            await callback_query.answer("Starting download...")
            await start_download(client, callback_query)
                
    except Exception as e:
        logger.error(f"Button callback error: {str(e)}")
        await callback_query.message.edit_text("❌ An error occurred. Please try again with /dl command")

async def start_download(client, callback_query):
    message = await callback_query.message.reply_text("Starting download...")
    user_id = callback_query.from_user.id
    
    try:
        content_data = user_data[user_id].get('content_data')
        content_playback = user_data[user_id].get('content_playback')
        selected_quality = user_data[user_id].get('quality')
        selected_audio = user_data[user_id].get('selected_audio', [])
        token = user_data[user_id].get('token')
        
        if not all([content_data, content_playback, selected_quality, selected_audio, token]):
            await message.edit_text("❌ Missing download information. Please try again!")
            return

        # Get content title and MPD URL
        content_title = content_data.get('name', 'video').replace(' ', '.').replace('/', '-')
        mpd_url = None
        for url_data in content_playback.get('playbackUrls', []):
            if url_data.get('streamtype') == 'dash':
                mpd_url = url_data.get('url')
                break
                
        if not mpd_url:
            await message.edit_text("❌ No valid playback URL found!")
            return
            
        await message.edit_text("⬇️ Starting download...")
        
        # Get selected languages for filename
        selected_languages = []
        for track in user_data[user_id].get('audio_tracks', []):
            if track['id'] in selected_audio:
                selected_languages.append(track['language'])
        
        # Start download
        output_file = await downloader.download_video(
            url=mpd_url,
            quality=int(selected_quality),
            audio_langs=selected_audio,
            message=message,
            content_title=content_title,
            selected_languages=selected_languages,
            token=token
        )
        
        if output_file and os.path.exists(output_file):
            file_size = os.path.getsize(output_file)
            if file_size > 2000 * 1024 * 1024:  # If file > 2GB
                await message.edit_text(f"✅ Download complete!\nFile size is too large for Telegram. File saved at: {output_file}")
            else:
                await message.edit_text("📤 Uploading to Telegram...")
                await client.send_video(
                    chat_id=callback_query.message.chat.id,
                    video=output_file,
                    caption=f"🎬 {content_title}\n🎥 {selected_quality}p\n🔊 Audio: {', '.join(selected_languages)}",
                    progress=progress_callback,
                    progress_args=(message,),
                    supports_streaming=True
                )
                await message.edit_text("✅ Download and upload complete!")
                
                # Cleanup
                try:
                    os.remove(output_file)
                except:
                    pass
        else:
            raise Exception("Download failed - No output file generated")
        
    except Exception as e:
        logger.error(f"Download error: {str(e)}")
        await message.edit_text(f"❌ Download failed: {str(e)}")

async def progress_callback(current, total, message):
    try:
        percent = current * 100 / total
        await message.edit_text(
            f"📤 Uploading:\n"
            f"Progress: {percent:.1f}%\n"
            f"Size: {format_size(current)}/{format_size(total)}"
        )
    except:
        pass

def format_size(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"

def main():
    # Check if required configs exist
    if not all(key in config for key in ['api_id', 'api_hash', 'bot_token']):
        print("Missing required configurations in config.json")
        print("Please ensure api_id, api_hash, and bot_token are set")
        return
    
    logger.info("Bot started successfully")
    print("Bot started! Press Ctrl+C to stop.")
    app.run()

if __name__ == '__main__':
    main()
