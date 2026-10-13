Local emoji picker assets for the chat POC.

- `index.js`, `picker.js`, `database.js`, `i18n/zh_CN.js`: `emoji-picker-element` 1.29.1, Apache-2.0, https://github.com/nolanlawson/emoji-picker-element . License: `LICENSE`.
- `zh-data.json`: `emoji-picker-element-data` 1.8.0 (`zh/emojibase/data.json`), Apache-2.0, https://www.npmjs.com/package/emoji-picker-element-data . License: `EMOJI-DATA-LICENSE.txt`.

Both the JavaScript modules and Chinese emoji data are served by this app under `/yuling-assets/emoji-picker/`. The picker sets its `dataSource` to that local JSON URL, so it does not request the library's default CDN.
