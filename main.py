# /// script
# requires-python = ">=3.11"
# dependencies = ["requests", "python-dotenv"]
# ///

import os
import sys
import json
import time
import re
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('x-to-telegram')

# ── Configuration ─────────────────────────────────────────────────────────

TWITTER_API_BASE = os.getenv('TWITTER_API_BASE', 'https://api.twitter.com/2').rstrip('/')
TWITTER_BEARER_TOKEN = os.getenv('TWITTER_BEARER_TOKEN')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.getenv('TELEGRAM_CHANNEL_ID')

TWITTER_USERNAME = os.getenv('TWITTER_USERNAME')
STATE_FILE = os.getenv('STATE_FILE', 'state.json')
# If SYNC_HASHTAG is empty, sync all tweets; otherwise filter by this hashtag
SYNC_HASHTAG = os.getenv('SYNC_HASHTAG', '').strip()
DELETE_CHECK_DAYS = int(os.getenv('DELETE_CHECK_DAYS', '7'))
REQUEST_TIMEOUT = int(os.getenv('REQUEST_TIMEOUT', '30'))
MAX_HISTORY_DAYS = int(os.getenv('MAX_HISTORY_DAYS', '60'))

MAX_CAPTION_LEN = 1024
MAX_TEXT_LEN = 4096

session = requests.Session()


def validate_config():
    missing = []
    if not TWITTER_BEARER_TOKEN:
        missing.append('TWITTER_BEARER_TOKEN')
    if not TELEGRAM_BOT_TOKEN:
        missing.append('TELEGRAM_BOT_TOKEN')
    if not TELEGRAM_CHANNEL_ID:
        missing.append('TELEGRAM_CHANNEL_ID')
    if not TWITTER_USERNAME:
        missing.append('TWITTER_USERNAME')
    if missing:
        logger.error("Missing required environment variables: %s", ', '.join(missing))
        logger.error("Please configure them in your .env file or environment.")
        sys.exit(1)


# ── State Management ──────────────────────────────────────────────────────

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Failed to parse state file %s: %s. Starting with empty state.", STATE_FILE, e)
            return {}
    return {}


def save_state(state):
    temp_file = f'{STATE_FILE}.tmp'
    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        os.replace(temp_file, STATE_FILE)
    except Exception as e:
        logger.error("Failed to save state to %s: %s", STATE_FILE, e)
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except OSError:
                pass


def tweet_id_to_timestamp_ms(tweet_id):
    """Extract creation timestamp (ms) from Twitter Snowflake ID."""
    try:
        return (int(tweet_id) >> 22) + 1288834974657
    except (ValueError, TypeError):
        return 0


def prune_old_msg_map(msg_map, max_days=MAX_HISTORY_DAYS):
    """Prune msg_map entries older than max_days to prevent state file bloat."""
    if not msg_map or max_days <= 0:
        return msg_map
    cutoff_ms = (time.time() - max_days * 86400) * 1000
    return {
        tid: val for tid, val in msg_map.items()
        if tweet_id_to_timestamp_ms(tid) > cutoff_ms
    }


def get_msg_entry(msg_map, tweet_id):
    entry = msg_map.get(tweet_id)
    if entry is None:
        return None
    if isinstance(entry, int):
        return {'id': entry, 'type': 'text'}
    return entry


# ── Official Twitter API v2 ───────────────────────────────────────────────

def twitter_headers():
    return {'Authorization': f'Bearer {TWITTER_BEARER_TOKEN}'}


def get_user_id(username, state):
    """Get Twitter user ID by username, caching result in state."""
    users_cache = state.setdefault('user_ids', {})
    if username in users_cache:
        return users_cache[username]

    url = f'{TWITTER_API_BASE}/users/by/username/{username}'
    logger.info("Looking up user ID for @%s via Twitter API v2...", username)
    r = session.get(url, headers=twitter_headers(), timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()

    if 'data' not in data:
        raise ValueError(f"User @{username} not found on Twitter")

    uid = str(data['data']['id'])
    users_cache[username] = uid
    save_state(state)
    logger.info("Found user ID for @%s: %s", username, uid)
    return uid


def get_tweets(user_id, since_id=None, max_pages=3):
    """Fetch user tweets via Twitter API v2."""
    url = f'{TWITTER_API_BASE}/users/{user_id}/tweets'
    params = {
        'max_results': 10,
        'expansions': 'attachments.media_keys,referenced_tweets.id,referenced_tweets.id.author_id',
        'media.fields': 'url,preview_image_url,type,variants,alt_text',
        'tweet.fields': 'created_at,entities,note_tweet,referenced_tweets,attachments',
        'exclude': 'retweets,replies',
    }
    if since_id:
        params['since_id'] = since_id

    all_tweets = []
    all_media = {}
    includes_tweets = {}
    includes_users = {}

    for page in range(max_pages):
        try:
            r = session.get(url, headers=twitter_headers(), params=params, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logger.error("Failed to fetch tweets (page %d): %s", page + 1, e)
            break

        tweets = data.get('data', [])
        if not tweets:
            break

        all_tweets.extend(tweets)

        # Collect includes
        includes = data.get('includes', {})
        for m in includes.get('media', []):
            if 'media_key' in m:
                all_media[m['media_key']] = m

        for t in includes.get('tweets', []):
            if 'id' in t:
                includes_tweets[t['id']] = t

        for u in includes.get('users', []):
            if 'id' in u:
                includes_users[u['id']] = u

        next_token = data.get('meta', {}).get('next_token')
        if not next_token:
            break
        params['pagination_token'] = next_token

    return {
        'tweets': all_tweets,
        'media': all_media,
        'quoted_tweets': includes_tweets,
        'users': includes_users,
    }


def check_deleted_tweets(msg_map):
    """Check tweets synced within DELETE_CHECK_DAYS; return deleted tweet IDs."""
    if DELETE_CHECK_DAYS <= 0 or not msg_map:
        return []
    cutoff_ms = (time.time() - DELETE_CHECK_DAYS * 86400) * 1000
    recent_ids = [
        tid for tid in msg_map
        if tweet_id_to_timestamp_ms(tid) > cutoff_ms
    ]
    if not recent_ids:
        return []

    deleted = []
    for i in range(0, len(recent_ids), 100):
        batch = recent_ids[i:i + 100]
        try:
            r = session.get(
                f'{TWITTER_API_BASE}/tweets',
                headers=twitter_headers(),
                params={'ids': ','.join(batch)},
                timeout=REQUEST_TIMEOUT,
            )
            if not r.ok:
                continue
            data = r.json()
            found = {t['id'] for t in data.get('data', [])}
            for tid in batch:
                if tid not in found:
                    deleted.append(tid)
        except Exception as e:
            logger.warning("Batch check tweets deletion failed: %s", e)
    return deleted


# ── Telegram API ──────────────────────────────────────────────────────────

def telegram_request(method, payload, max_retries=3):
    """Send request to Telegram Bot API with rate-limit retry support."""
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}'
    for attempt in range(max_retries):
        try:
            r = session.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            if r.status_code == 429:
                retry_after = 2
                try:
                    retry_after = r.json().get('parameters', {}).get('retry_after', 2)
                except Exception:
                    pass
                logger.warning(
                    "Telegram 429 rate limit hit. Sleeping %d seconds (attempt %d/%d)...",
                    retry_after, attempt + 1, max_retries,
                )
                time.sleep(retry_after)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if attempt == max_retries - 1:
                raise
            logger.warning("Telegram request %s failed (%s), retrying...", method, e)
            time.sleep(1)
    raise RuntimeError(f"Telegram API {method} failed after {max_retries} retries")


def send_telegram_text(text, reply_to=None):
    first_msg_id = None
    chunks = [text[i:i + MAX_TEXT_LEN] for i in range(0, len(text), MAX_TEXT_LEN)] if text else ['']
    current_reply = reply_to
    for chunk in chunks:
        payload = {'chat_id': TELEGRAM_CHANNEL_ID, 'text': chunk}
        if current_reply:
            payload['reply_to_message_id'] = current_reply
        res = telegram_request('sendMessage', payload)
        msg_id = res['result']['message_id']
        if first_msg_id is None:
            first_msg_id = msg_id
        current_reply = msg_id
        if len(chunks) > 1:
            time.sleep(0.5)
    return first_msg_id


def send_telegram_photo(media_url, text, media_type='photo', reply_to=None):
    method = 'sendVideo' if media_type == 'video' else 'sendPhoto'
    field = 'video' if media_type == 'video' else 'photo'

    caption = text
    overflow_text = None
    if len(caption) > MAX_CAPTION_LEN:
        caption = caption[:MAX_CAPTION_LEN - 3] + '...'
        overflow_text = text

    payload = {'chat_id': TELEGRAM_CHANNEL_ID, field: media_url}
    if caption:
        payload['caption'] = caption
    if reply_to:
        payload['reply_to_message_id'] = reply_to

    res = telegram_request(method, payload)
    msg_id = res['result']['message_id']

    if overflow_text:
        time.sleep(0.5)
        send_telegram_text(overflow_text, reply_to=msg_id)

    return msg_id


def send_telegram_media_group(media_items, text, reply_to=None):
    media = [{'type': item['type'], 'media': item['url']} for item in media_items]
    caption = text
    overflow_text = None
    if len(caption) > MAX_CAPTION_LEN:
        caption = caption[:MAX_CAPTION_LEN - 3] + '...'
        overflow_text = text

    if caption:
        media[0]['caption'] = caption

    payload = {'chat_id': TELEGRAM_CHANNEL_ID, 'media': media}
    if reply_to:
        payload['reply_to_message_id'] = reply_to

    res = telegram_request('sendMediaGroup', payload)
    msg_id = res['result'][0]['message_id']

    if overflow_text:
        time.sleep(0.5)
        send_telegram_text(overflow_text, reply_to=msg_id)

    return msg_id


def delete_telegram_message(message_id):
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteMessage'
    payload = {'chat_id': TELEGRAM_CHANNEL_ID, 'message_id': message_id}
    try:
        r = session.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        if r.status_code == 400:
            logger.info("Notice: Message %s already removed or cannot be deleted.", message_id)
            return
        r.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Failed to delete Telegram message %s: %s", message_id, e)


# ── Formatting & Extraction ───────────────────────────────────────────────

def expand_urls(text, entities, filter_tweet_id=None):
    """Expand t.co short URLs and filter media/quote links."""
    if not entities:
        return text
    for url_obj in entities.get('urls', []):
        t_co = url_obj.get('url', '')
        expanded = url_obj.get('expanded_url', '')
        if filter_tweet_id and expanded.endswith(f'/status/{filter_tweet_id}'):
            text = text.replace(t_co, '').strip()
        elif any(x in expanded for x in ['pic.twitter.com', '/i/web/', 'x.com/i/', 'twitter.com/i/', '/photo/', '/video/']):
            text = text.replace(t_co, '').strip()
        elif t_co:
            text = text.replace(t_co, expanded)
    return text


def build_text(tweet, filter_tweet_id=None):
    """Construct text content for Telegram, handling Note Tweet and entities."""
    note = tweet.get('note_tweet', {})
    text = note.get('text') or tweet.get('text', '')
    entities = note.get('entities') or tweet.get('entities')

    text = expand_urls(text, entities, filter_tweet_id=filter_tweet_id)

    if SYNC_HASHTAG:
        text = re.sub(rf'#{re.escape(SYNC_HASHTAG)}\b', '', text, flags=re.IGNORECASE)
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()

    return text


def _best_video_url(media_obj):
    """Select the highest bitrate mp4 URL from media variants."""
    variants = media_obj.get('variants', [])
    mp4s = [v for v in variants if v.get('content_type') == 'video/mp4']
    if mp4s:
        return max(mp4s, key=lambda v: v.get('bit_rate', 0)).get('url')
    return media_obj.get('url') or media_obj.get('preview_image_url')


def get_media_items(tweet, media_map):
    """Extract media items (photo/video) from tweet attachments."""
    keys = tweet.get('attachments', {}).get('media_keys', [])
    items = []
    for k in keys:
        media = media_map.get(k)
        if not media:
            continue
        m_type = media.get('type')
        if m_type == 'photo':
            url = media.get('url')
            if url:
                items.append({'url': url, 'type': 'photo'})
        elif m_type in ('video', 'animated_gif'):
            url = _best_video_url(media)
            if url:
                items.append({'url': url, 'type': 'video'})
    return items


def send_tweet(tweet, media_items, reply_to=None, quote_fallback=None, filter_tweet_id=None):
    text = build_text(tweet, filter_tweet_id=filter_tweet_id)
    if quote_fallback:
        text = f"{text}\n\n{quote_fallback}".strip() if text else quote_fallback

    if not media_items:
        return send_telegram_text(text, reply_to=reply_to), 'text'
    elif len(media_items) == 1:
        item = media_items[0]
        return send_telegram_photo(item['url'], text, media_type=item['type'], reply_to=reply_to), 'media'
    else:
        return send_telegram_media_group(media_items, text, reply_to=reply_to), 'media'


# ── Main Entry ────────────────────────────────────────────────────────────

def main():
    validate_config()

    state = load_state()
    msg_map = state.get('msg_map', {})
    msg_map = prune_old_msg_map(msg_map)

    # 1. Lookup User ID
    user_id = get_user_id(TWITTER_USERNAME, state)

    # 2. Check deleted tweets
    deleted_ids = check_deleted_tweets(msg_map)
    for tweet_id in deleted_ids:
        entry = get_msg_entry(msg_map, tweet_id)
        if entry:
            delete_telegram_message(entry['id'])
            msg_map.pop(tweet_id, None)
            logger.info("Deleted Telegram message corresponding to deleted tweet: %s", tweet_id)

    # 3. Fetch new tweets
    last_tweet_id = state.get('last_tweet_id')
    feed = get_tweets(user_id, since_id=last_tweet_id)
    tweets = feed['tweets']
    media_map = feed['media']
    quoted_tweets_map = feed['quoted_tweets']
    users_map = feed['users']

    if not tweets:
        logger.info("No new tweets found for @%s.", TWITTER_USERNAME)
        state['msg_map'] = msg_map
        save_state(state)
        return

    # Process in reverse order (chronological order) so Telegram timeline is preserved
    synced_count = 0
    for tweet in reversed(tweets):
        tweet_id = str(tweet['id'])

        # Determine whether to sync this tweet
        has_sync_tag = True
        if SYNC_HASHTAG:
            note_entities = tweet.get('note_tweet', {}).get('entities', {})
            tweet_entities = tweet.get('entities', {})
            hashtags = note_entities.get('hashtags') or tweet_entities.get('hashtags', [])
            tag_in_entities = any(
                h.get('tag', '').lower() == SYNC_HASHTAG.lower() or h.get('text', '').lower() == SYNC_HASHTAG.lower()
                for h in hashtags
            )
            full_text = (tweet.get('note_tweet', {}).get('text') or tweet.get('text', '')).lower()
            tag_in_text = f'#{SYNC_HASHTAG.lower()}' in full_text
            has_sync_tag = tag_in_entities or tag_in_text

        if has_sync_tag:
            media_items = get_media_items(tweet, media_map)

            # Check if this tweet quotes another tweet
            quoted_id = None
            for ref in tweet.get('referenced_tweets', []):
                if ref.get('type') == 'quoted':
                    quoted_id = str(ref.get('id', ''))
                    break

            reply_to = None
            quote_fallback = None

            if quoted_id:
                entry = get_msg_entry(msg_map, quoted_id)
                if entry:
                    reply_to = entry['id']
                else:
                    quoted_t = quoted_tweets_map.get(quoted_id, {})
                    author_id = quoted_t.get('author_id')
                    quoted_author = users_map.get(author_id, {}).get('username', '')
                    if quoted_author:
                        quote_fallback = f"🔗 Quoting @{quoted_author}: https://x.com/{quoted_author}/status/{quoted_id}"
                    else:
                        quote_fallback = f"🔗 Quoting: https://x.com/i/status/{quoted_id}"

            try:
                msg_id, msg_type = send_tweet(
                    tweet,
                    media_items,
                    reply_to=reply_to,
                    quote_fallback=quote_fallback,
                    filter_tweet_id=quoted_id,
                )
                msg_map[tweet_id] = {'id': msg_id, 'type': msg_type}
                synced_count += 1
                logger.info(
                    "Synced tweet %s (TG msg %s)%s",
                    tweet_id, msg_id, ' [reply]' if reply_to else '',
                )
                time.sleep(1)  # Flood rate limiting
            except Exception as e:
                logger.error("Failed to sync tweet %s: %s", tweet_id, e)
        else:
            logger.info("Skipped tweet %s (missing #%s)", tweet_id, SYNC_HASHTAG)

    try:
        max_id = max(tweets, key=lambda t: int(t['id']))['id']
        state['last_tweet_id'] = str(max_id)
    except Exception as e:
        logger.warning("Could not determine max tweet id: %s", e)

    state['msg_map'] = msg_map
    save_state(state)
    logger.info("Sync finished: %d of %d tweets synced.", synced_count, len(tweets))


if __name__ == '__main__':
    main()
