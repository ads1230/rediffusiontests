# App Review test assets

Files a reviewer can paste into reDiffusion to see a populated guide and play
something, without owning a tuner.

- `review-channels.m3u` — eight broadcaster-published, free-to-view HLS streams
  (France 24 English, DW English, TRT World, Arirang TV, NASA TV, Red Bull TV) plus
  two content-free test streams (Akamai live test pattern, Apple's HLS sample).
  Every URL is the broadcaster's own; nothing comes from an aggregator.
- `review-epg.xml` — XMLTV for exactly those channels. Listings for the four news
  channels come from the public epg.pw country guides, re-keyed to the playlist's
  `tvg-id`s; the other four get generic hourly blocks so the grid is never blank.
- `build_review_assets.py` — regenerates both files. epg.pw publishes roughly one
  day ahead, so run it daily where the files are hosted:

  ```sh
  python3 build_review_assets.py --out /var/www/review
  ```

  Network failures fall back to generic blocks rather than an empty guide.

## Hosting

Upload both files to an HTTPS location you control, then put the two URLs into
`APP_REVIEW_NOTES.md` (and App Store Connect). The playlist's `x-tvg-url` points at
`review-epg.xml` next to it, so keep them in the same directory.

## Refreshing after stream changes

Broadcasters move their live URLs occasionally. Before each submission:

```sh
grep -E '^https?://' review-channels.m3u | while read u; do
  printf '%s %s\n' "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -r 0-1024 "$u")" "$u"
done
```

Anything not returning 200 or 206 should be swapped or removed, and the matching
entry in `CHANNELS` in the script updated.

## Hosted copy

This repository is the hosted copy. The files are served over HTTPS at:

- Playlist: https://raw.githubusercontent.com/ads1230/rediffusiontests/main/review-channels.m3u
- Guide: https://raw.githubusercontent.com/ads1230/rediffusiontests/main/review-epg.xml

A GitHub Actions workflow (`.github/workflows/refresh-epg.yml`) rebuilds the guide
every day and commits it when it changes.
