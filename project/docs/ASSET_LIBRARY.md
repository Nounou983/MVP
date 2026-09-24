# Real furniture asset library — Phase 8

The catalog now includes a curated first batch of **real, textured furniture models and product previews from Poly Haven**, whose furniture models are published under CC0. The catalog stores the provider, asset ID, source URL, license and model/preview URLs so the provenance is explicit.

Selected assets include sofas, an armchair, dining chair, coffee table, dining table, cabinet and bed. Poly Haven documents these models as CC0 and provides glTF downloads and preview imagery.

## Important commercial note

These CC0 assets remove the **technical/demo asset bottleneck**. They are not a substitute for a retailer's own branded product catalog. Before presenting a specific manufacturer's product as a product for sale, replace the demo asset with the manufacturer's licensed photography/model and keep the product SKU, price and availability authoritative.

## Asset fields

```json
{
  "assets": {
    "thumbnail": "...",
    "photos": ["..."],
    "model": { "url": "...", "format": "gltf" },
    "source": {
      "provider": "Poly Haven",
      "asset_id": "...",
      "license": "CC0-1.0",
      "source_url": "..."
    },
    "is_placeholder": false
  }
}
```

## Replacing assets

Replace only the `assets` block for a product. The placement/rendering/UI code does not need to change.
