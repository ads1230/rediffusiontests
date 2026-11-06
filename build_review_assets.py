#!/usr/bin/env python3
"""Builds the App Review test playlist and its matching XMLTV guide.

Streams are Apple's public HLS examples, BBC R&D's public test card simulcast
and Blender Foundation open movies (CC BY 3.0) from their publishers' test
CDNs — no broadcaster channels, nothing copyrighted beyond the open licences
(policy 2026-11-06). None of them has public listings, so the guide is made of
"Test <genre>" slots that cycle through every genre colour the app draws; the
epg.pw path stays in the code for any channel given an (epg country, epg
channel id) source.

Run it from a cron job on the host that serves the files so dead streams are
dropped promptly.

    python3 build_review_assets.py [--out DIR] [--days 2] [--cache DIR]
"""
import argparse
import datetime as dt
import os
import re
import sys
import urllib.request
import xml.sax.saxutils as sx

EPG_BASE = "https://epg.pw/xmltv/epg_{country}.xml"
USER_AGENT = "Mozilla/5.0 (reDiffusion review-assets builder)"

# tvg-id, display name, stream URL, group, logo, (epg country, epg channel id) or None
# Content policy (2026-11-06): ONLY Apple's own HLS example streams, BBC R&D's
# public test card simulcast (a real live HLS stream, rdmedia.bbc.co.uk) and
# Blender Foundation open movies (Big Buck Bunny, Tears of Steel — CC BY 3.0)
# served from their publishers' public test CDNs (Apple, Mux, Unified
# Streaming). BBC R&D's Big Buck Bunny / Elephants Dream are DASH-only, which
# the app does not play, so they are not listed. No broadcaster channels: even
# free-to-view news feeds are copyrighted programming and the review playlist
# must not look like a channel aggregator (the earlier list — France 24, Al
# Jazeera, DW, NHK World, Bloomberg, TRT World, Arirang, Red Bull TV — was
# dropped for that reason). Apple's sample loop is channel 1.
# The generator re-verifies master → variant → segment on every run and drops
# anything that no longer plays (see stream_is_alive).
# Logos: PNG renders from Wikimedia Commons (the app does not draw SVG), resolved
# through the Commons API so the hashed paths are real. They double as the
# programme image: every <programme> carries <icon src=logo> because these
# channels have no per-programme artwork.
COMMONS = "https://upload.wikimedia.org/wikipedia/commons/"
COLOR_BARS_LOGO = COMMONS + "thumb/6/60/SMPTE_Color_Bars_16x9.svg/960px-SMPTE_Color_Bars_16x9.svg.png"
BBB_LOGO = COMMONS + "thumb/c/c5/Big_buck_bunny_poster_big.jpg/960px-Big_buck_bunny_poster_big.jpg"
TOS_LOGO = COMMONS + "thumb/7/70/Tos-poster.png/960px-Tos-poster.png"
APPLE_EXAMPLES = "https://devstreaming-cdn.apple.com/videos/streaming/examples/"
BBC_RD_TESTCARD = "https://rdmedia.bbc.co.uk/testcard/simulcast/manifests/"
CHANNELS = [
    ("apple.bipbop", "Apple HLS Sample Loop",
     APPLE_EXAMPLES + "img_bipbop_adv_example_ts/master.m3u8",
     "Test", COLOR_BARS_LOGO, None),
    # Live (not VOD): the channels that exercise the live pipeline. The
    # numbers keep them apart: the app merges same-name streams and strips
    # quality markers (HEVC, HD, 4K…) when comparing, so "Test Card" and
    # "Test Card HEVC" would collapse into one channel with two variants.
    ("bbc.rd.testcard", "BBC R&D Test Card 1",
     BBC_RD_TESTCARD + "avc-full.m3u8",
     "Test", COLOR_BARS_LOGO, None),
    ("bbc.rd.testcard.hevc", "BBC R&D Test Card 2 HEVC",
     BBC_RD_TESTCARD + "hevc-ctv.m3u8",
     "Test", COLOR_BARS_LOGO, None),
    ("apple.bipbop.fmp4", "Apple HLS Sample (fMP4)",
     APPLE_EXAMPLES + "img_bipbop_adv_example_fmp4/master.m3u8",
     "Test", COLOR_BARS_LOGO, None),
    ("apple.bipbop.16x9", "Apple HLS Sample 16:9",
     APPLE_EXAMPLES + "bipbop_16x9/bipbop_16x9_variant.m3u8",
     "Test", COLOR_BARS_LOGO, None),
    ("apple.bipbop.4x3", "Apple HLS Sample 4:3",
     APPLE_EXAMPLES + "bipbop_4x3/bipbop_4x3_variant.m3u8",
     "Test", COLOR_BARS_LOGO, None),
    ("mux.bbb", "Big Buck Bunny",
     "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8",
     "Blender", BBB_LOGO, None),
    ("mux.tos", "Tears of Steel",
     "https://test-streams.mux.dev/tos_ismc/main.m3u8",
     "Blender", TOS_LOGO, None),
    ("unified.tos", "Tears of Steel (fMP4)",
     "https://demo.unified-streaming.com/k8s/features/stable/video/tears-of-steel/tears-of-steel.ism/.m3u8",
     "Blender", TOS_LOGO, None),
]

# Schedule for channels without public listings. Every test channel cycles
# through the same "Test <genre>" slots so the guide shows all seven of the
# app's genre colours (news, sport, drama/movies, kids, documentary,
# entertainment/comedy, music); varied lengths and a per-channel start offset
# keep the grid from lining up into identical columns. Entries are
# (title, minutes, [XMLTV categories]) — the categories are what the app's
# classifier keys on (EPGModels.keywordsByGenre).
TEST_SLOTS = [
    ("Test News", 30, ["News"]),
    ("Test Comedy", 30, ["Comedy", "Entertainment"]),
    ("Test Sport", 60, ["Sport"]),
    ("Test Drama", 45, ["Drama"]),
    ("Test Kids", 30, ["Children"]),
    ("Test Documentary", 60, ["Documentary"]),
    ("Test Music", 30, ["Music"]),
    ("Test Movie", 90, ["Movie", "Film"]),
    ("Test Game Show", 30, ["Game Show", "Entertainment"]),
    ("Test Weather", 15, ["Weather", "News"]),
]
# Group names must avoid the app's VOD markers (movie, film, series, cinema,
# vod…): LiveTVM3UParser.isVODEntry files such entries under VOD, and they
# then never become live channels for the guide to match ("Open Movies" did
# exactly that on 2026-11-06).
# Per-channel overrides keep the content channels honest about what plays.
SYNTHETIC = {
    "mux.bbb": [("Big Buck Bunny", 60, ["Animation", "Children"]), ("Test Comedy", 30, ["Comedy"])],
    "mux.tos": [("Tears of Steel", 60, ["Sci-Fi", "Movie"]), ("Test Drama", 30, ["Drama"])],
    "unified.tos": [("Tears of Steel", 60, ["Sci-Fi", "Movie"]), ("Test Documentary", 30, ["Documentary"])],
}


def stream_is_alive(url, timeout=20):
    """Master → first variant → last segment (ranged GET). A 200 on the master
    alone proved worthless: dead channels keep serving a master for months."""
    import urllib.parse

    def fetch(u, rng=None):
        headers = {"User-Agent": "AppleCoreMedia/1.0.0 (iPhone; U; CPU OS 27_1 like Mac OS X)"}
        if rng:
            headers["Range"] = rng
        req = urllib.request.Request(u, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), resp.geturl()

    try:
        body, final = fetch(url)
        text = body.decode("utf-8", "replace")
        if not text.lstrip().startswith("#EXTM3U"):
            return False, "master is not an M3U8"
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if "#EXT-X-STREAM-INF" in text:
            variant = urllib.parse.urljoin(final, next(l for l in lines if not l.startswith("#")))
            body, final = fetch(variant)
            text = body.decode("utf-8", "replace")
            if not text.lstrip().startswith("#EXTM3U"):
                return False, "variant is not an M3U8"
            lines = [l.strip() for l in text.splitlines() if l.strip()]
        segments = [l for l in lines if not l.startswith("#")]
        if not segments:
            return False, "media playlist has no segments"
        fetch(urllib.parse.urljoin(final, segments[-1]), "bytes=0-65535")
        return True, "live" if "#EXT-X-ENDLIST" not in text else "vod"
    except Exception as exc:  # any HTTP/network failure means a reviewer would see a spinner
        return False, f"{type(exc).__name__}: {str(exc)[:80]}"


def fetch_epg(country, cache_dir):
    path = os.path.join(cache_dir, f"epg_{country}.xml") if cache_dir else None
    if path and os.path.exists(path) and (dt.datetime.now().timestamp() - os.path.getmtime(path)) < 6 * 3600:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", "replace")
    req = urllib.request.Request(EPG_BASE.format(country=country), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = resp.read()
    if path:
        os.makedirs(cache_dir, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
    return data.decode("utf-8", "replace")


PROGRAMME_RE = re.compile(r'<programme\b([^>]*)>(.*?)</programme>', re.S)
ATTR_RE = re.compile(r'(\w+)="([^"]*)"')
TITLE_RE = re.compile(r'<title[^>]*>(.*?)</title>', re.S)

# Programme categories (user request 2026-10-24: "for the example EPG can we
# have categories … for the programs"). The public listings carry none, so
# they are inferred from the title with the channel's playlist group as the
# fallback. Vocabulary matches the app's genre classifier (EPGModels
# EPGProgrammeGenre: sports / documentary / music / entertainment / movies /
# news / kids) so every row gets a genre colour. First matching rule wins;
# specific genres sit above the broad ones, as in the app.
CATEGORY_RULES = [
    (["Children", "Education"], ["little charo", "kids", "children", "cartoon"]),
    (["Sport"], ["sport", "scoreline", "football", "racing", "red bull", "cycling", "motor"]),
    (["Music"], ["k-pop", "music", "concert", "vibe in action"]),
    (["Film", "Animation"], ["big buck bunny", "cinema", "movie", " film"]),
    (["Business", "News"], ["money", "business", "biz", "profit", "wall street", "market", "bloomberg", "the deal",
                            "economy", "primer", "rubenstein", "the circuit", "accelerators"]),
    (["Arts", "Culture"], ["arts24", "paris des arts", "the art of", "k-culture", "kabuki", "culture", "manga",
                           "mitateru", "classical japanese", "beauty", "design", "short cuts", "storyteller"]),
    (["Travel", "Documentary"], ["trip in", "somewhere street", "across africa", "countryside", "journey", "travel",
                                 "escape", "time-lapse"]),
    (["Technology", "Science"], ["tech", "science", "digital"]),
    (["Lifestyle", "Culture"], ["fashion", "food", "cooking", "pairing"]),
    (["Documentary"], ["documentary", "japanology", "samurai", "behind the scenes", "zero waste", "degrees",
                       "history", "nature", "wildlife", "life", "360", "witness", "101 east", "fault lines",
                       "investigates", "revisited", "listening post", "explains"]),
    (["Current Affairs", "News"], ["inside story", "focus", "observers", "reporters", "newsmakers", "talking europe",
                                   "roundtable", "debrief", "the 51 percent", "french connections", "world this week",
                                   "nexus", "debate", "people and", "moo no plan", "strait", "head to head",
                                   "counting the cost"]),
    (["Talk Show", "Entertainment"], ["show", "tete a tete", "entre nous", "guest of the day", "laugh", "interview",
                                      "talk", "wakey", "i'm live"]),
    (["News"], ["news", "newshour", "newsline", "newsfeed", "live", "weather"]),
]
GROUP_CATEGORIES = {"News": ["News"], "Sport": ["Sport"], "Test": ["Entertainment"]}


def categories_for(title, group):
    lowered = title.lower()
    for categories, keywords in CATEGORY_RULES:
        if any(k in lowered for k in keywords):
            return categories
    return GROUP_CATEGORIES.get(group, ["Entertainment"])


def category_xml(categories):
    return "".join(f'\n    <category lang="en">{sx.escape(c)}</category>' for c in categories)


def extract_programmes(xml_text, source_id, new_id, group="News", icon_url=None):
    """Return the programme elements for one channel, re-keyed to new_id.
    Programmes without their own <icon> get the channel logo as their image;
    programmes without their own <category> get inferred ones."""
    out = []
    for m in PROGRAMME_RE.finditer(xml_text):
        attrs = dict(ATTR_RE.findall(m.group(1)))
        if attrs.get("channel") != source_id:
            continue
        attrs["channel"] = new_id
        attr_text = " ".join(f'{k}="{sx.escape(v, {chr(34): "&quot;"})}"' for k, v in attrs.items())
        body = m.group(2)
        # Drop empty <desc /> and the epg.pw audio block; keep title/desc/category/date.
        body = re.sub(r'\s*<desc\s*/>', "", body)
        body = re.sub(r'\s*<audio>.*?</audio>', "", body, flags=re.S)
        # epg.pw tags English-language listings "zh" on some feeds; these channels are English.
        body = body.replace(' lang="zh"', ' lang="en"')
        if "<category" not in body:
            title_match = TITLE_RE.search(body)
            title = sx.unescape(title_match.group(1)) if title_match else ""
            body = body.rstrip() + category_xml(categories_for(title, group))
        if icon_url and "<icon" not in body:
            body = body.rstrip() + f'\n    <icon src="{sx.escape(icon_url, {chr(34): "&quot;"})}" />'
        body = body.rstrip() + "\n  "
        out.append((attrs.get("start", ""), f"  <programme {attr_text}>{body}</programme>"))
    out.sort()
    return [p for _, p in out]


def synthetic_programmes(new_id, blocks, days, start=None, icon_url=None, group="News", offset=0):
    """Generic programme slots. `blocks` entries are (title, minutes) or
    (title, minutes, [categories]); `offset` rotates the starting slot so
    neighbouring channels do not show identical columns."""
    out = []
    icon = f'\n    <icon src="{sx.escape(icon_url, {chr(34): "&quot;"})}" />' if icon_url else ""
    if start is None:
        start = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0) - dt.timedelta(hours=6)
    end = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0) + dt.timedelta(days=days)
    i = offset
    t = start
    while t < end:
        block = blocks[i % len(blocks)]
        title, minutes = block[0], block[1]
        categories = list(block[2]) if len(block) > 2 else categories_for(title, group)
        stop = t + dt.timedelta(minutes=minutes)
        out.append(
            f'  <programme start="{t:%Y%m%d%H%M%S} +0000" stop="{stop:%Y%m%d%H%M%S} +0000" channel="{new_id}">\n'
            f'    <title lang="en">{sx.escape(title)}</title>\n'
            f'    <desc lang="en">Test programme slot ({sx.escape(", ".join(categories))}) for the review playlist. The stream is a test loop; this title only exercises the guide.</desc>'
            f'{category_xml(categories)}{icon}\n'
            f'  </programme>'
        )
        t = stop
        i += 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--days", type=int, default=2, help="span of synthetic listings")
    ap.add_argument("--cache", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".epg-cache"))
    ap.add_argument("--epg-url", default="review-epg.xml",
                    help="value for the playlist's x-tvg-url (use the absolute hosted URL when publishing)")
    ap.add_argument("--keep-dead", action="store_true",
                    help="publish channels even when their stream fails the end-to-end check")
    args = ap.parse_args()

    epg_cache = {}
    channel_xml = []
    programme_xml = []
    report = []
    published = []
    for index, (tvg_id, name, url, group, logo, source) in enumerate(CHANNELS):
        alive, detail = stream_is_alive(url)
        if not alive and not args.keep_dead:
            report.append(f"{name}: DROPPED, stream failed ({detail})")
            continue
        if not alive:
            report.append(f"{name}: WARNING stream failed ({detail}) but kept")
        published.append((tvg_id, name, url, group, logo, source))
        icon = f'\n    <icon src="{sx.escape(logo)}" />' if logo else ""
        channel_xml.append(f'  <channel id="{tvg_id}">\n    <display-name lang="en">{sx.escape(name)}</display-name>{icon}\n  </channel>')
        progs = []
        if source:
            country, source_id = source
            try:
                if country not in epg_cache:
                    epg_cache[country] = fetch_epg(country, args.cache)
                progs = extract_programmes(epg_cache[country], source_id, tvg_id, group=group, icon_url=logo or None)
            except Exception as exc:  # network trouble: fall back to generic blocks
                report.append(f"{name}: epg.pw {country} unavailable ({exc}); using generic blocks")
        if not progs:
            progs = synthetic_programmes(tvg_id, SYNTHETIC.get(tvg_id, TEST_SLOTS), args.days,
                                         icon_url=logo or None, group=group, offset=index)
            report.append(f"{name}: {len(progs)} generic blocks")
        else:
            starts = re.findall(r'start="(\d{8})(\d{4})', "\n".join(progs))
            report.append(f"{name}: {len(progs)} listings {starts[0][0]} {starts[0][1]} to {starts[-1][0]} {starts[-1][1]} UTC")
            # Public listings can stop a few hours out (DW covered 7 h on 2026-10-24):
            # pad past the last real stop with generic blocks so the grid never
            # goes blank before the next daily rebuild.
            last_stop = max(re.findall(r'stop="(\d{14})', "\n".join(progs)))
            resume = dt.datetime.strptime(last_stop, "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc)
            padding = synthetic_programmes(tvg_id, TEST_SLOTS, args.days, start=resume, icon_url=logo or None, group=group, offset=index)
            if padding:
                report[-1] += f" (+{len(padding)} generic blocks to fill the horizon)"
            progs.extend(padding)
        programme_xml.extend(progs)

    now = dt.datetime.now(dt.timezone.utc)
    xmltv = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<!DOCTYPE tv SYSTEM \"xmltv.dtd\">\n"
        f'<tv date="{now:%Y%m%d%H%M%S} +0000" generator-info-name="reDiffusion review assets" '
        'source-info-name="epg.pw (public XMLTV) + broadcaster streams">\n'
        + "\n".join(channel_xml) + "\n" + "\n".join(programme_xml) + "\n</tv>\n"
    )
    m3u_lines = [f'#EXTM3U x-tvg-url="{args.epg_url}"']
    for tvg_id, name, url, group, logo, _ in published:
        logo_attr = f' tvg-logo="{logo}"' if logo else ""
        m3u_lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{name}"{logo_attr} group-title="{group}",{name}')
        m3u_lines.append(url)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "review-epg.xml"), "w", encoding="utf-8") as f:
        f.write(xmltv)
    with open(os.path.join(args.out, "review-channels.m3u"), "w", encoding="utf-8") as f:
        f.write("\n".join(m3u_lines) + "\n")
    print("\n".join(report))
    print(f"wrote {os.path.join(args.out, 'review-channels.m3u')} and review-epg.xml "
          f"({len(channel_xml)} channels, {len(programme_xml)} programmes)")
    # Fail the run (and the Actions job) if the list has thinned out badly.
    return 0 if len(published) >= 5 else 1


if __name__ == "__main__":
    sys.exit(main())
