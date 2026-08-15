# MVP eye design and asset provenance

- **Design:** `mvp-eye-svg-v1`
- **Implementation owner:** Voice Agent v2 project
- **Source:** [`web/src/avatar/eye/MvpEyeModule.ts`](../../web/src/avatar/eye/MvpEyeModule.ts) and [`mvpEye.css`](../../web/src/avatar/eye/mvpEye.css)
- **Repository-use approval:** GitHub issue #9 and accepted ADR-0002 / Design Gate V

The eye is original code-authored SVG geometry created for Voice Agent v2: an almond outline, circular iris/pupil, two orbital rings, and eight cardinal/diagonal ticks. It contains no image, font, character model, shader, proprietary asset, copied path, Live2D/3D material, or generated third-party artwork. The implementation uses only browser SVG and CSS primitives and is distributed under the repository owner's project terms.

No legacy avatar source was inspected or migrated for this slice. The pinned legacy repository remains provenance-only evidence for possible later modules.
