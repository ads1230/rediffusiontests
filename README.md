# App Review test assets

Files a reviewer can paste into reDiffusion to see a populated guide and play
something, without owning a tuner.

- `review-channels.m3u` — broadcaster-published, free-to-view HLS streams (France 24
  English, Al Jazeera English, DW English, NHK World-Japan, Bloomberg TV Europe,
  TRT World, Arirang TV, Red Bull TV) plus two content-free VOD samples (Apple's
  HLS example, Big Buck Bunny from Mux). Every URL is the broadcaster's own;
  nothing comes from an aggregator.
- `review-epg.xml` — XMLTV for exactly those channels. Listings for the news
  channels come from the public epg.pw country guides, re-keyed to the playlist's
  `tvg-id`s; the rest get generic hourly blocks so the grid is never blank.
- `build_review_assets.py` — regenerates both files. Before writing, it plays each
  stream end to end (master, variant, a segment) and drops any that fail, because a
  master playlist can keep answering for months after a channel dies. epg.pw
  publishes one to three days ahead, so run it daily where the files are hosted:

  ```sh
  python3 build_review_assets.py --out /var/www/review
  ```

  Network failures fall back to generic blocks rather than an empty guide.

## Hosting

Upload both files to an HTTPS location you control, then put the two URLs into
`APP_REVIEW_NOTES.md` (and App Store Connect). The playlist's `x-tvg-url` points at
`review-epg.xml` next to it, so keep them in the same directory.

## Refreshing after stream changes

Broadcasters move their live URLs occasionally. The generator's own check reports
`DROPPED, stream failed (...)` for each one; when that happens, find the
broadcaster's current URL (the iptv-org index is a convenient place to look it up,
but only take URLs on the broadcaster's own domain) and update `CHANNELS`. The run
exits non-zero if fewer than five channels survive, which fails the Actions job.

## Hosted copy

This repository is the hosted copy. The files are served over HTTPS at:

- Playlist: https://raw.githubusercontent.com/ads1230/rediffusiontests/main/review-channels.m3u
- Guide: https://raw.githubusercontent.com/ads1230/rediffusiontests/main/review-epg.xml

A GitHub Actions workflow (`.github/workflows/refresh-epg.yml`) rebuilds the guide
every day and commits it when it changes.
