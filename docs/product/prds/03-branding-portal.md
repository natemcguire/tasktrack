# F03 — Branding and the customer portal

**Wave:** 1 · **Dependencies:** F01, F02 · **Review:** product owner + customer-view review

## Outcome

Each agency has its own mark and display name. A customer's project presents
“Agency × Customer” using the two marks without distorting them. Customer home
shows shared progress, Comms and eligible invoices. Neither customer assets nor
the deployment's identity are baked into public code, demo fixtures or screenshots.

## Screens and behavior

[Branding](../wireframes.html#branding) · [Customer home](../wireframes.html#portal).
Settings → Branding contains agency name, simple monogram fallback, logo upload,
accent color and preview. Customer settings contains display name/logo. The first
release accepts PNG/WebP/JPEG and a normalized server-generated monogram. Uploaded
SVG is not served raw; defer it or sanitize/rasterize in a dedicated safe worker.

Place marks in bounded 32–40px header slots, `object-fit:contain`, auto width,
max-width160px, with a typographic × separator. Preserve the source aspect ratio.
If a mark is missing or fails, show its display name; never render a broken image.
Mobile may wrap the customer name but must not crop either logo. Provide alt text
once, avoid repeating adjacent text to screen readers, and reject inaccessible
accent choices or use accessible neutral controls.

Customer home lists only granted projects. Within one project: brief shared
summary, progress, recent shared activity, “Ask a question”, and upcoming approved
milestones. Invoice navigation appears only with a billing-contact grant. Internal
time, token cost, margins, notes, runner logs and internal participant lists never
enter its response. Internal preview uses the same serializer under an explicit
preview context, not a cosmetic role switch.

## Data and storage

`tenant_brand(version,display_name,mark_asset_id,accent,updated_by)` and
`customer_brand(customer_id,version,display_name,mark_asset_id)` reference private
validated assets. `brand_asset(id,tenant_id,digest,mime,width,height,bytes,status)`
is immutable after normalization. Limit2MiB and4096×4096 pixels; reject animated
images, decompression bombs, corrupt formats and MIME/extension mismatch. Strip
metadata, produce appropriately sized variants, and keep an original only if the
tenant retention policy allows it. Upload by bytes; do not fetch arbitrary URLs.

Brand versions are referenced by issued invoice snapshots and share previews.
Changing a logo updates future views/documents, never silently rewrites an issued
invoice. Delete unused variants with delayed garbage collection; retain assets
referenced by financial documents for their retention period.

## API and access

- `GET/PATCH /branding`: public-safe configuration to members; update admin-only,
  expected version. Field validation returns422 with accessible color guidance.
- `POST /brand-assets`: bounded multipart; returns202 normalization operation.
- `PATCH /customers/{id}/branding`: admin-only, asset must belong to tenant.
- `GET /projects/{id}/portal`: current authorized customer projection including
  capabilities, shared milestones and activity; internal preview explicit.
- `GET /brand-assets/{id}/content`: authenticate parent scope unless attached to
  an explicit public share or issued capability document. Do not expose R2 directly.

Events: branding.updated, customer.branding_updated, brand_asset.ready/rejected.
No outbound customer email solely because a logo changed. Cache keys include
tenant/customer/brand version and authorization class; no shared CDN cache of
private portal HTML.

## Share links and privacy

Existing task sharing continues to emit server-side Open Graph metadata and a
1200×630 PNG preview, with safe title/status/customer update only. Branding is
snapshot-versioned. Customer comments require membership even when arriving through
a public link. Revocation removes future page/image access but cannot recall an
image already cached by Messages or WhatsApp. The share UI explains that once.

## Acceptance and migration

1. Two agencies uploading the same filename receive isolated assets and branding.
2. Wide/tall/square logos retain proportions at390px and1440px; missing/broken
   assets produce a usable name fallback. No page-level horizontal overflow.
3. External portal responses, source HTML, search and OG images contain no internal
   notes, cost fields or other customers' names.
4. Malicious image/SVG/metadata payloads cannot execute or escape private storage.
5. Existing unbranded tenants render the neutral Tasktrack fallback after upgrade.
6. Invoice/share snapshots keep the old approved mark after a branding update.
7. Verify keyboard upload/error flow, contrast and actual mobile screenshots.

Ship generic assets and fictional test data. Tenant-specific initial marks are
uploaded to tenant storage through the same API any other agency uses.
