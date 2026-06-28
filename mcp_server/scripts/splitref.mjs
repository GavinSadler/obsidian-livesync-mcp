// Standalone reference port of splitPieces2V2 (text path,
// plainSplit=true, useSegmenter=false) from
// livesync-commonlib/src/string_and_binary/chunks.ts.
//
// Used to regenerate the parity fixtures in tests/test_chunks.py
// (_PARITY_FIXTURES) when the upstream algorithm changes.
//
// Usage:
//   echo "your text" | node scripts/splitref.mjs <PIECE_SIZE> <MIN_CHUNK_SIZE>
//
// Output: one JSON-encoded chunk per line.

const MAX_ITEMS = 100;

function* chunkStringGenerator(source, maxLength) {
    const strLen = source.length;
    if (strLen > maxLength) {
        let from = 0;
        do {
            let end = from + maxLength;
            if (end > strLen) {
                yield source.substring(from);
                break;
            }
            while (source.charCodeAt(end - 1) != source.codePointAt(end - 1)) {
                end++;
            }
            yield source.substring(from, end);
            from = end;
        } while (from < strLen);
    } else {
        yield source;
    }
}

function* chunkStringGeneratorFromGenerator(sources, maxLength) {
    for (const source of sources) {
        yield* chunkStringGenerator(source, maxLength);
    }
}

function* stringGenerator(sources) {
    for (const str of sources) yield str;
}

function* splitByDelimiterWithMinLength(sources, delimiter, minimumChunkLength = 25, splitThreshold) {
    let buf = "";
    let last = false;
    const dl = delimiter.length;
    for (const source of sources) {
        const max = source.length;
        if (splitThreshold && max > splitThreshold) {
            yield buf + source;
            last = false;
            buf = "";
            continue;
        }
        let i = -1;
        let prev = 0;
        L1: do {
            i = source.indexOf(delimiter, prev);
            if (i == -1) break L1;
            buf += source.slice(prev, i) + delimiter;
            if (buf.length > minimumChunkLength) {
                yield buf;
                buf = "";
                last = false;
            } else {
                last = true;
            }
            prev = i + dl;
        } while (i < max);
        if (prev != i || (prev == -1 && i == -1)) {
            buf += source.slice(prev);
            last = true;
        }
    }
    if (last) yield buf;
}

function splitTextV2(text, pieceSize, minimumChunkSize) {
    if (text.length === 0) return [];
    const textLen = text.length;
    let xMinimumChunkSize = minimumChunkSize;
    while (textLen / xMinimumChunkSize > MAX_ITEMS) {
        xMinimumChunkSize += minimumChunkSize;
    }
    const org = stringGenerator([text]);
    const gen1 = splitByDelimiterWithMinLength(org, "\n", xMinimumChunkSize);
    const gen = chunkStringGeneratorFromGenerator(gen1, pieceSize);
    return [...gen];
}

const [, , pieceSizeStr, minChunkStr] = process.argv;
const pieceSize = parseInt(pieceSizeStr, 10);
const minChunk = parseInt(minChunkStr, 10);

let buf = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (d) => (buf += d));
process.stdin.on("end", () => {
    const chunks = splitTextV2(buf, pieceSize, minChunk);
    for (const c of chunks) process.stdout.write(JSON.stringify(c) + "\n");
});
