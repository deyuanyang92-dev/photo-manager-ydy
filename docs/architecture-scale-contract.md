# Large-project architecture contract

This application is designed for a future catalogue of hundreds of thousands
of projects and millions of photos. That scale is a baseline constraint, not a
later performance enhancement.

## Non-negotiable rules

- Never create one Qt widget per project, specimen, or photo for an unbounded
  result set. Render only the current viewport or a bounded page with a virtual
  model/delegate.
- Never load an unbounded result set merely to calculate a count or populate a
  screen. Counts and filters belong in indexed database queries; list and grid
  queries must be paged with stable sort keys.
- Do not rescan the complete filesystem on navigation or refresh. Maintain a
  persistent media/project index and update it incrementally from filesystem
  events, with an explicit repair/reindex operation for recovery.
- Photo decoding and disk thumbnail-cache access stay off the GUI thread.
  Decode only the visible band plus a bounded prefetch band.
- Loading must be visually atomic. Keep the last valid frame, or show a static
  opaque local cover, while data/layout/thumbnails are prepared; commit a
  complete first viewport in one GUI update. Never clear the screen and then
  reveal rows or photos one by one.
- Navigation changes the data-generation token. Results from older project,
  workspace, filter, or page requests must be discarded before painting.
- Caches are bounded and invalidated by file identity/change, not by ordinary
  navigation. Switching pages must not delete reusable memory or disk
  thumbnails.
- Large-result UI must state `shown / total` and provide paging or virtual
  scrolling. A silent hard cap that makes records appear missing is forbidden.

## Verification gates

- Add focused tests for stale-result rejection, visible-band-only thumbnail
  work, bounded widget/model growth, and atomic first-frame reveal.
- Include large synthetic fixtures in performance checks. Small fixtures can
  prove correctness but cannot prove scalability.
- Do not claim million-photo readiness while any active path still performs
  full filesystem scans, materializes all rows, or creates per-item widgets.
