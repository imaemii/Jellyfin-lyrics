import logging
from tinytag import TinyTag
import os
import httpx
import asyncio
from functools import partial

# Configure logging to screen
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Edit this to be your folder with all your music in it
directory_path = r'Z:\mnt\data\Jellyfin\Music'
cache_file = "lyrics_cache.txt"
MAX_CONCURRENCY = 20  # number of simultaneous requests

def load_cache():
    if os.path.exists(cache_file):
        with open(cache_file, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    return []

def save_cache(cache):
    with open(cache_file, "w", encoding="utf-8") as f:
        for item in sorted(set(cache)):
            f.write(item + "\n")

def is_in_cache(file_path, cache_entries):
    for entry in cache_entries:
        if os.path.isdir(entry):
            if os.path.commonpath([file_path, entry]) == entry:
                return True
        else:
            if file_path == entry:
                return True
    return False

async def get_lyrics(client, artist, title, album, duration):
    url = "https://lrclib.net/api/get"
    params = {
        "artist_name": artist,
        "track_name": title,
        "album_name": album,
        "duration": duration
    }
    try:
        r = await client.get(url, params=params, timeout=10.0)
        if r.status_code == 200:
            data = r.json()
            lyrics = data.get("syncedLyrics") or data.get("plainLyrics")
            if lyrics:
                logging.info("Found lyrics for: %s - %s", artist, title)
                return lyrics
    except Exception as e:
        logging.error("HTTP error for %s - %s (%s)", artist, title, e)
    logging.info("Lyrics not found for: %s - %s", artist, title)
    return None

def get_song_details(file_path):
    audio = TinyTag.get(file_path)
    return audio.album, audio.title, audio.artist, int(audio.duration)

async def process_file(file_path, client, cache_entries, sem):
    if is_in_cache(file_path, cache_entries):
        return "cached", file_path

    new_file_path = os.path.splitext(file_path)[0] + '.lrc'
    if os.path.exists(new_file_path):
        return "already_exists", file_path

    try:
        # TinyTag is sync, so run it in a threadpool
        loop = asyncio.get_running_loop()
        album, title, artist, duration = await loop.run_in_executor(
            None, partial(get_song_details, file_path)
        )
        if not artist or not title:
            return "missing", file_path
    except Exception as e:
        return "error", (file_path, e)

    async with sem:  # limit concurrency
        lyrics = await get_lyrics(client, artist, title, album, duration)

    if lyrics is None:
        return "missing", file_path

    try:
        with open(new_file_path, 'w', encoding="utf-8") as f:
            f.write(lyrics)
        return "found", file_path
    except Exception as e:
        return "error", (file_path, e)

def collect_audio_files(directory_path):
    audio_files = []
    for root, _, files in os.walk(directory_path):
        for file in files:
            if file.endswith(('.flac', '.mp3', '.wav', '.ogg', '.aac', '.wma')):
                audio_files.append(os.path.join(root, file))
    return audio_files

async def main_async():
    Found_lyrics = 0
    Missing_lyrics = 0

    logging.info("Starting the lyrics fetching process...")
    logging.info("Using cache file: %s", os.path.abspath(cache_file))

    audio_files = collect_audio_files(directory_path)
    total_files = len(audio_files)
    cache_entries = load_cache()

    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async with httpx.AsyncClient() as client:
        tasks = [process_file(fp, client, cache_entries, sem) for fp in audio_files]
        for idx, coro in enumerate(asyncio.as_completed(tasks), start=1):
            result = await coro

            if result[0] == "cached":
                logging.info("Skipping (in cache): %s", result[1])
                continue
            elif result[0] == "already_exists":
                logging.info("Lyrics file already exists for: %s", result[1])
                cache_entries.append(result[1])
            elif result[0] == "found":
                Found_lyrics += 1
                cache_entries.append(result[1])
                logging.info("Saved lyrics for: %s", result[1])
            elif result[0] == "missing":
                Missing_lyrics += 1
                logging.info("No lyrics found for: %s", result[1])
            elif result[0] == "error":
                Missing_lyrics += 1
                logging.error("Error processing %s (%s)", result[1][0], result[1][1])

    save_cache(cache_entries)

    logging.info("Lyrics fetching complete.")
    logging.info("Total songs processed: %s", total_files)
    logging.info("Total with lyrics found: %s", Found_lyrics)
    logging.info("Total with lyrics missing: %s", Missing_lyrics)

def main():
    asyncio.run(main_async())

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Exiting due to keyboard interrupt.")
