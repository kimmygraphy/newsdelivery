"""뉴스보이 데일리 TOP10 → JSON 저장 + Discord 전송"""
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

URL = "https://newsboy.news/news-list/daily-top10"
KST = ZoneInfo("Asia/Seoul")
DATA_DIR = Path("data")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "ko-KR,ko;q=0.9",
}


# ---------- 수집 ----------
def get_html():
    """최대 3번 재시도하며 페이지 HTML을 가져온다."""
    for attempt in range(1, 4):
        try:
            res = requests.get(URL, headers=HEADERS, timeout=(10, 60))
            res.raise_for_status()
            return res.text
        except requests.exceptions.RequestException as e:
            print(f"요청 {attempt}회 실패: {e}")
            if attempt == 3:
                raise
            time.sleep(15)


def clean(text):
    """순위('1', '1위'), '뉴스N건 분석', 'play' 같은 조각을 제거한다."""
    text = re.sub(r"^\s*\d+\s*(\d+\s*위)?", "", text)
    text = re.sub(r"뉴스\s*\d+\s*건\s*(분석)?", "", text)
    text = re.sub(r"\bplay\b", "", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_item(a):
    """링크 하나에서 제목·부제·기사 수를 뽑는다.
    링크 안에 제목이 두 번 등장하므로, 두 번 나온 텍스트를 제목으로 본다."""
    raw = [p for p in a.get_text("|").split("|") if p.strip()]
    m = re.search(r"뉴스\s*(\d+)\s*건", " ".join(raw))
    texts = [t for t in (clean(p) for p in raw) if len(t) >= 6]
    if not texts:
        return None

    repeated = [t for t, c in Counter(texts).most_common() if c >= 2]
    title = repeated[0] if repeated else texts[0]
    others = [t for t in texts if t != title]
    return {
        "title": title,
        "summary": others[0] if others else "",
        "article_count": int(m.group(1)) if m else None,
    }


def fetch_top10():
    soup = BeautifulSoup(get_html(), "html.parser")
    items, seen = [], set()
    for a in soup.find_all("a", href=re.compile(r"/news/\d+")):
        url = urljoin(URL, a["href"])
        if url in seen:
            continue
        parsed = parse_item(a)
        if not parsed:
            continue
        seen.add(url)
        items.append({"rank": len(items) + 1, **parsed, "url": url})
        if len(items) == 10:
            break

    period = soup.find(string=re.compile(r"\d+시\s*~\s*\d+시"))
    return items, (period.strip() if period else "")


# ---------- 저장 ----------
def save(date_str, period, items):
    DATA_DIR.mkdir(exist_ok=True)
    payload = {
        "date": date_str,
        "period": period,
        "crawled_at": datetime.now(KST).isoformat(timespec="seconds"),
        "items": items,
    }
    (DATA_DIR / f"{date_str}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # Phase 2 달력용 날짜 목록
    index_path = DATA_DIR / "index.json"
    dates = json.loads(index_path.read_text()) if index_path.exists() else []
    index_path.write_text(json.dumps(sorted(set(dates) | {date_str}), indent=2))


# ---------- 전송 ----------
def send_discord(webhook, now, period, items):
    lines = []
    for it in items:
        count = f" `{it['article_count']}건`" if it["article_count"] else ""
        lines.append(f"**{it['rank']}.** [{it['title']}]({it['url']}){count}")

    weekday = "월화수목금토일"[now.weekday()]
    embed = {
        "title": f"📰 데일리 TOP10 · {now.month}/{now.day}({weekday})",
        "description": "\n".join(lines),
        "footer": {"text": f"뉴스보이 · {period}".rstrip(" ·")},
        "color": 0x2F6BFF,
    }
    requests.post(webhook, json={"embeds": [embed]}, timeout=20).raise_for_status()


def main():
    now = datetime.now(KST)
    date_str = now.strftime("%Y-%m-%d")

    items, period = fetch_top10()
    if len(items) < 10:
        sys.exit(f"헤드라인을 {len(items)}개만 찾았어요. 사이트 구조 변경 여부를 확인하세요.")

    save(date_str, period, items)
    print(f"[{date_str}] {period} 저장 완료")
    for it in items:
        print(f"{it['rank']:>2}. {it['title']}")

    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if webhook:
        send_discord(webhook, now, period, items)
        print("Discord 전송 완료")


if __name__ == "__main__":
    main()
