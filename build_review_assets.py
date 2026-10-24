#!/usr/bin/env python3
"""Builds the App Review test playlist and its matching XMLTV guide.

Streams are broadcaster-published, free-to-view HLS URLs (no aggregator links).
Guide data comes from the public epg.pw country files, trimmed to just these
channels and re-keyed to the tvg-ids used in the playlist. Channels with no
public listings (test patterns, Red Bull TV, NASA TV) get generic hourly blocks
so the grid is never empty.

Run it from a cron job on the host that serves the files; epg.pw covers about a
day ahead, so a daily run keeps the guide populated.

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
CHANNELS = [
    ("france24.en", "France 24 English",
     "https://static.france24.com/live/F24_EN_HI_HLS/live_web.m3u8",
     "News", "https://upload.wikimedia.org/wikipedia/commons/8/8a/France24.png",
     ("DE", "76807")),
    ("dw.en", "DW English",
     "https://dwamdstream102.akamaized.net/hls/live/2015525/dwstream102/index.m3u8",
     "News", "https://upload.wikimedia.org/wikipedia/commons/6/69/Deutsche_Welle_symbol_2012.svg",
     ("FR", "55871")),
    ("trtworld", "TRT World",
     "https://tv-trtworld.medya.trt.com.tr/master.m3u8",
     "News", "https://upload.wikimedia.org/wikipedia/commons/4/4c/TRT_World_logo.svg",
     ("GB", "12173")),
    ("arirang", "Arirang TV",
     "https://amdlive-ch01-ctnd-com.akamaized.net/arirang_1ch/smil:arirang_1ch.smil/playlist.m3u8",
     "News", "https://upload.wikimedia.org/wikipedia/commons/2/2e/Arirang_TV_logo.svg",
     ("GB", "12037")),
    ("nasa.tv", "NASA TV",
     "https://ntv1.akamaized.net/hls/live/2014075/NASA-NTV1-HLS/master.m3u8",
     "Science", "https://upload.wikimedia.org/wikipedia/commons/e/e5/NASA_logo.svg",
     None),
    ("redbull.tv", "Red Bull TV",
     "https://rbmn-live.akamaized.net/hls/live/590964/BoRB-AT/master.m3u8",
     "Sport", "https://upload.wikimedia.org/wikipedia/commons/f/f5/RedBullEnergyDrink.svg",
     None),
    ("akamai.test", "Akamai Live Test Pattern",
     "https://cph-p2p-msl.akamaized.net/hls/live/2000341/test/master.m3u8",
     "Test", "",
     None),
    ("apple.bipbop", "Apple HLS Sample (HEVC)",
     "https://devstreaming-cdn.apple.com/videos/streaming/examples/img_bipbop_adv_example_hevc/master.m3u8",
     "Test", "",
     None),
]

# Generic schedule for channels without public listings: (title, minutes).
SYNTHETIC = {
    "nasa.tv": [("NASA TV Live Coverage", 60), ("ISS Live Views", 60), ("NASA Science Hour", 60)],
    "redbull.tv": [("Red Bull TV Live", 60), ("Sports Highlights", 30), ("Documentary Block", 90)],
    "akamai.test": [("Test Pattern", 60)],
    "apple.bipbop": [("HLS Sample Loop", 30)],
}


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


def extract_programmes(xml_text, source_id, new_id):
    """Return the programme elements for one channel, re-keyed to new_id."""
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
        out.append((attrs.get("start", ""), f"  <programme {attr_text}>{body}</programme>"))
    out.sort()
    return [p for _, p in out]


def synthetic_programmes(new_id, blocks, days, start=None):
    out = []
    if start is None:
        start = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0) - dt.timedelta(hours=6)
    end = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0) + dt.timedelta(days=days)
    i = 0
    t = start
    while t < end:
        title, minutes = blocks[i % len(blocks)]
        stop = t + dt.timedelta(minutes=minutes)
        out.append(
            f'  <programme start="{t:%Y%m%d%H%M%S} +0000" stop="{stop:%Y%m%d%H%M%S} +0000" channel="{new_id}">\n'
            f'    <title lang="en">{sx.escape(title)}</title>\n'
            f'    <desc lang="en">Live stream published by the broadcaster. No programme listings are available for this channel.</desc>\n'
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
    args = ap.parse_args()

    epg_cache = {}
    channel_xml = []
    programme_xml = []
    report = []
    for tvg_id, name, url, group, logo, source in CHANNELS:
        icon = f'\n    <icon src="{sx.escape(logo)}" />' if logo else ""
        channel_xml.append(f'  <channel id="{tvg_id}">\n    <display-name lang="en">{sx.escape(name)}</display-name>{icon}\n  </channel>')
        progs = []
        if source:
            country, source_id = source
            try:
                if country not in epg_cache:
                    epg_cache[country] = fetch_epg(country, args.cache)
                progs = extract_programmes(epg_cache[country], source_id, tvg_id)
            except Exception as exc:  # network trouble: fall back to generic blocks
                report.append(f"{name}: epg.pw {country} unavailable ({exc}); using generic blocks")
        if not progs:
            progs = synthetic_programmes(tvg_id, SYNTHETIC.get(tvg_id, [("Live", 60)]), args.days)
            report.append(f"{name}: {len(progs)} generic blocks")
        else:
            starts = re.findall(r'start="(\d{8})(\d{4})', "\n".join(progs))
            report.append(f"{name}: {len(progs)} listings {starts[0][0]} {starts[0][1]} to {starts[-1][0]} {starts[-1][1]} UTC")
            # Public listings can stop a few hours out (DW covered 7 h on 2026-10-24):
            # pad past the last real stop with generic blocks so the grid never
            # goes blank before the next daily rebuild.
            last_stop = max(re.findall(r'stop="(\d{14})', "\n".join(progs)))
            resume = dt.datetime.strptime(last_stop, "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc)
            padding = synthetic_programmes(tvg_id, [(f"{name} Live", 60)], args.days, start=resume)
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
    for tvg_id, name, url, group, logo, _ in CHANNELS:
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
