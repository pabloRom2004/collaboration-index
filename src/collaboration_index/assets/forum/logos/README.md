# Replay provider logos

These SVG marks come from `@lobehub/icons-static-svg` version 1.95.1
([Lobe Icons](https://github.com/lobehub/lobe-icons), MIT license retained here).
`providers.json` maps model developer names to their mark; Claude, Gemini,
Qwen and Kimi use their model-family marks. The replay identifies the model
family before falling back to provider names in the route, so an OpenAI-compatible
or OpenRouter transport does not replace the model developer's logo.

Add an SVG and registry entry here, and its model/route aliases in the
`PROVIDERS` list in `../replay.html`. `collaboration_index/replay.py` embeds these
assets as data URLs, along with the license, in every generated HTML replay,
keeping it self-contained.
Regenerate an older replay to include updated logos. Unknown models retain the
neutral AI label.
