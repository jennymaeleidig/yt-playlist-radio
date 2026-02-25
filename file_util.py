import os
import json
import logging

logger = logging.getLogger(__name__)

def _load_urls_from_file(path: str):
    urls = []
    if not path:
        logger.debug("No path provided to load_urls_from_file")
        return urls
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    continue
                if " #" in line:
                    line = line.split(" #", 1)[0].strip()
                urls.append(line)
        logger.info("Loaded %d URLs from %s", len(urls), path)
    except FileNotFoundError:
        logger.warning("URL file not found: %s", path)
    except Exception:
        logger.exception("Failed to read URL file: %s", path)
    return urls

def _create_or_get_cache(cache_file_path: str):
    try:
        if os.path.exists(cache_file_path):
            with open(cache_file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        else:
            return {}
    except Exception:
        logger.exception("Failed to load cache file, starting with empty cache")
        return {}

def _save_cache(cache_data: dict, cache_file_path: str):
    try:
        with open(cache_file_path, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
    except Exception:
        logger.exception("Failed to save cache to %s", cache_file_path)
