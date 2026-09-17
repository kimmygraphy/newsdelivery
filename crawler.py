"""뉴스보이 데일리 TOP10 → JSON 저장 + Discord 전송

22:00~05:00(KST) 사이 30분마다 실행된다.
- 그날 기록이 이미 저장됐으면 바로 종료
- 사이트가 다운돼 실패하면 다음 30분 실행에서 다시 시도
- 05:00 실행까지 실패하면 Discord로 실패 알림
"""
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta
from datetime import time as dtime
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

FINAL_FROM = dtime(21, 30)    # 이 시각 이후 저장된 기록을 그날의 최종본으로 봄
NEXT_DAY_UNTIL = 7            # 자정~7시 전 실행은 전날 기록을 채우는 재시도로 봄
GIVE_UP_AT = dtime(5, 0)      # 이 시각 이후 실행에서도 실패하면 포기


# ---------- 날짜 판단 ----------
def target_date(now):
    if now.hour < NEXT_DAY_UNTIL:
        return (now - timedelta(days=1)).date()
    return now.date()


def already_final(d):
    path = DATA_DIR / f"{d.isoformat()}.json"
    if not path.exists():
        return False
    crawled_at = json.loads(path.read_text(encoding="utf-8")).get("crawled_at")
    if not crawled_at:
        return False
    return datetime.fromisoformat(crawled_at) >= datetime.combine(d, FINAL_FROM, tzinfo=KST)


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
    page_date = soup.find(string=re.compile(r"\d{1,2}월\s*\d{1,2}일\s*\S요일"))
    page_md = None
    if page_date:
        m = re.search(r"(\d{1,2})월\s*(\d{1,2})일", page_date)
        page_md = (int(m.group(1)), int(m.group(2)))
    return items, (period.strip() if period else ""), page_md


# ---------- 저장 ----------
def save(d, period, items, now):
    DATA_DIR.mkdir(exist_ok=True)
    date_str = d.isoformat()
    payload = {
        "date": date_str,
        "period": period,
        "crawled_at": now.isoformat(timespec="seconds"),
        "items": items,
    }
    (DATA_DIR / f"{date_str}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 달력용 날짜 목록
    index_path = DATA_DIR / "index.json"
    dates = json.loads(index_path.read_text()) if index_path.exists() else []
    index_path.write_text(json.dumps(sorted(set(dates) | {date_str}), indent=2))


# ---------- 전송 ----------
def weekday_label(d):
    return f"{d.month}/{d.day}({'월화수목금토일'[d.weekday()]})"


def post_discord(webhook, embed):
    requests.post(webhook, json={"embeds": [embed]}, timeout=20).raise_for_status()


def send_top10(webhook, d, period, items):
    lines = []
    for it in items:
        count = f" `{it['article_count']}건`" if it["article_count"] else ""
        lines.append(f"**{it['rank']}.** [{it['title']}]({it['url']}){count}")
    post_discord(webhook, {
        "title": f"📰 데일리 TOP10 · {weekday_label(d)}",
        "description": "\n".join(lines),
        "footer": {"text": f"뉴스보이 · {period}".rstrip(" ·")},
        "color": 0x2F6BFF,
    })


def send_failure(webhook, d, reason):
    post_discord(webhook, {
        "title": f"⚠️ 데일리 TOP10 · {weekday_label(d)}",
        "description": "새벽 5시까지 뉴스보이에서 헤드라인을 가져오지 못해 이날 기록은 비어 있어요.",
        "footer": {"text": str(reason)[:200]},
        "color": 0xB0B5BD,
    })


# ---------- 실행 ----------
def main():
    now = datetime.now(KST)
    target = target_date(now)
    manual = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")

    if not manual and already_final(target):
        print(f"[{target}] 이미 저장됨, 종료")
        return

    try:
        items, period, page_md = fetch_top10()
        if page_md and page_md != (target.month, target.day):
            raise ValueError(f"페이지 날짜({page_md[0]}/{page_md[1]})가 기록할 날짜와 달라요")
        if len(items) < 10:
            raise ValueError(f"헤드라인을 {len(items)}개만 찾았어요")
    except Exception as e:
        if manual:
            sys.exit(f"수집 실패: {e}")
        if now.hour < NEXT_DAY_UNTIL and now.time() >= GIVE_UP_AT:
            if webhook:
                send_failure(webhook, target, e)
            sys.exit(f"[{target}] 최종 실패: {e}")
        print(f"[{target}] 수집 실패, 30분 뒤 다시 시도해요: {e}")
        return

    save(target, period, items, now)
    print(f"[{target}] {period} 저장 완료")
    for it in items:
        print(f"{it['rank']:>2}. {it['title']}")

    if webhook:
        send_top10(webhook, target, period, items)
        print("Discord 전송 완료")


if __name__ == "__main__":
    main()
