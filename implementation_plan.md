# Implementation Plan: Fundamental DOM Token Reduction

## Goal Description
Drastically reduce the size of the injected DOM skeleton by removing redundant information ("Fundamental Compression").

## Proposed Changes

### `drivers/js_loader.py` (The Compressor)
1. **Viewport Filter**: Add `getBoundingClientRect()` check. If `top > window.innerHeight * 2`, ignore the element (unless it's part of a known list structure).
2. **Class Noise Filter**:
   - If `className` contains > 5 spaces (likely Tailwind), truncate it or drop it entirely unless it contains specific keywords like `btn`, `nav`, `item`.
3. **Wrapper Flattening**:
   - If a `div` has no ID, no Title, no specialized attributes, and only 1 child, return `traverse(child)` directly. Eliminate the container.
4. **Text Truncation**: Reduce `MAX_TEXT_LEN` from 80 -> 50 chars.

### `skills/observer.py` (The Cache)
1. **MD5 Caching**: Implement the hashing logic agreed upon previously.

## Verification
1. **Compare Sizes**:
   - Before: ~30k chars.
   - After: Expecting ~15k chars.
2. **Functionality Check**: Ensure navigation still works (buttons are still visible in the compressed DOM).
