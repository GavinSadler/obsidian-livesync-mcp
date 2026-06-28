# Note with Emoji and CJK

## UTF-16 Edge Case Testing

This note tests UTF-16 length handling for characters that don't fit in a single UTF-16 code unit.

### Emoji Examples

- 🚀 Rocket (U+1F680, requires surrogate pair in UTF-16)
- 😀 Grinning face (U+1F600, requires surrogate pair)
- ❤️ Red heart (U+2764, requires variation selector)
- 👨‍👩‍👧‍👦 Family (ZWJ sequence, multiple code units)

### CJK Characters

- 中文 (Chinese)
- 日本語 (Japanese)
- 한국어 (Korean)
- 한글 테스트

### Mixed Content

The quick brown fox 🦊 jumps over 中文 text and emoji sequences 👍🎉.

This is important because:
1. JavaScript's `.length` counts UTF-16 code units, not Unicode code points
2. Emoji and CJK may require multiple code units
3. Chunk boundaries must account for this to avoid splitting multi-unit characters

Test string with emoji at boundary: 你好世界🌍
