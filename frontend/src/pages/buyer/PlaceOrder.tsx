import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { buyerApi } from "../../api/client";
import Layout from "../../components/Layout";
import BulkOrderSheet from "../../components/BulkOrderSheet";
import {
  CATALOG,
  CATEGORIES,
  CatalogItem,
  discountPct,
  PRICE_BUCKETS,
  SORT_OPTIONS,
} from "../../data/catalog";
import { HandshakeIcon, LayersIcon, SearchIcon, ShieldCheckIcon } from "../../components/icons";

type SortId = (typeof SORT_OPTIONS)[number]["id"];

export default function PlaceOrder() {
  const { token } = useAuth();
  const navigate = useNavigate();
  const [selected, setSelected] = useState<CatalogItem | null>(null);
  const [categories, setCategories] = useState<Set<string>>(new Set());
  const [priceBuckets, setPriceBuckets] = useState<Set<string>>(new Set());
  const [sort, setSort] = useState<SortId>("relevance");
  const [query, setQuery] = useState("");

  function toggleCategory(c: string) {
    setCategories((prev) => {
      const next = new Set(prev);
      if (next.has(c)) next.delete(c);
      else next.add(c);
      return next;
    });
  }

  function togglePriceBucket(id: string) {
    setPriceBuckets((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function clearFilters() {
    setCategories(new Set());
    setPriceBuckets(new Set());
  }

  const filtersActive = categories.size > 0 || priceBuckets.size > 0;

  const items = useMemo(() => {
    const filtered = CATALOG.filter((item) => {
      const matchesCategory = categories.size === 0 || categories.has(item.category);
      const matchesQuery = item.name.toLowerCase().includes(query.trim().toLowerCase());
      const matchesPrice =
        priceBuckets.size === 0 ||
        PRICE_BUCKETS.some((b) => priceBuckets.has(b.id) && item.price >= b.min && item.price < b.max);
      return matchesCategory && matchesQuery && matchesPrice;
    });

    const sorted = [...filtered];
    if (sort === "price-asc") sorted.sort((a, b) => a.price - b.price);
    else if (sort === "price-desc") sorted.sort((a, b) => b.price - a.price);
    else if (sort === "discount-desc") sorted.sort((a, b) => discountPct(b) - discountPct(a));
    return sorted;
  }, [categories, priceBuckets, sort, query]);

  async function handleSubmit(args: { totalQty: number; qualityMin: number; deadline: string; buyerRef: string }) {
    if (!token || !selected) return;
    const res = await buyerApi.placeOrder(token, {
      buyer_ref: args.buyerRef,
      product_type: selected.productType,
      total_qty: args.totalQty,
      quality_min: args.qualityMin,
      deadline: args.deadline,
    });
    navigate(`/buyer/orders/${res.order_id}`);
  }

  const searchSlot = (
    <div className="search-bar">
      <SearchIcon />
      <input
        aria-label="Search products"
        placeholder="Search bags, apparel, home décor…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
    </div>
  );

  return (
    <Layout search={searchSlot}>
      <div className="page page-wide">
        <div className="promo-banner">
          <div className="promo-banner-copy">
            <h1>One supplier. A whole consortium behind it.</h1>
            <p>
              Every listing here is bulk capacity pooled across trust-scored SFURTI workshops — you deal
              with Saathi, we deal with coordination.
            </p>
          </div>
          <div className="promo-stats">
            <div className="promo-stat">
              <span className="promo-stat-icon">
                <ShieldCheckIcon />
              </span>
              Trust-scored workshops
            </div>
            <div className="promo-stat">
              <span className="promo-stat-icon">
                <LayersIcon />
              </span>
              MOQ bulk pricing
            </div>
            <div className="promo-stat">
              <span className="promo-stat-icon">
                <HandshakeIcon />
              </span>
              Single accountable supplier
            </div>
          </div>
        </div>

        <div className="shop-layout">
          <aside className="filter-panel">
            <div className="filter-panel-header">
              <h2 className="filter-title">Filters</h2>
              {filtersActive && (
                <button type="button" className="filter-clear" onClick={clearFilters}>
                  Clear all
                </button>
              )}
            </div>
            <div className="filter-sections">
              <div className="filter-section">
                <h3 className="filter-title">Category</h3>
                {CATEGORIES.map((c) => (
                  <label className="filter-check-row" key={c}>
                    <input
                      type="checkbox"
                      checked={categories.has(c)}
                      onChange={() => toggleCategory(c)}
                    />
                    {c}
                  </label>
                ))}
              </div>
              <div className="filter-section">
                <h3 className="filter-title">Price</h3>
                {PRICE_BUCKETS.map((b) => (
                  <label className="filter-check-row" key={b.id}>
                    <input
                      type="checkbox"
                      checked={priceBuckets.has(b.id)}
                      onChange={() => togglePriceBucket(b.id)}
                    />
                    {b.label}
                  </label>
                ))}
              </div>
            </div>
          </aside>

          <div className="shop-results">
            <div className="results-toolbar">
              <span className="results-count">{items.length} products</span>
              <select
                className="sort-select"
                aria-label="Sort products"
                value={sort}
                onChange={(e) => setSort(e.target.value as SortId)}
              >
                {SORT_OPTIONS.map((opt) => (
                  <option key={opt.id} value={opt.id}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>

            {items.length === 0 ? (
              <div className="card empty-state">
                <p>No products match your search{filtersActive ? " and filters" : ""}.</p>
                {filtersActive && (
                  <button type="button" className="btn btn-secondary" onClick={clearFilters}>
                    Clear filters
                  </button>
                )}
              </div>
            ) : (
              <div className="catalog-grid">
                {items.map((item) => {
                  const pct = discountPct(item);
                  return (
                    <button
                      key={item.productType}
                      className="product-card"
                      onClick={() => setSelected(item)}
                    >
                      <div className="product-swatch" style={{ background: item.swatch }}>
                        <span>{item.emoji}</span>
                        <span className="discount-badge">{pct}% OFF</span>
                      </div>
                      <div className="product-info">
                        <span className="product-category">{item.category}</span>
                        <span className="product-name">{item.name}</span>
                        <div className="product-price-row">
                          <span className="product-price">₹{item.price}</span>
                          <span className="product-mrp">₹{item.factoryPrice}</span>
                          <span className="product-off">{pct}% off</span>
                        </div>
                        <div className="product-bottom-row">
                          <span className="verified-badge">✓ Verified</span>
                          <span className="product-moq">MOQ {item.moq}</span>
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>

      {selected && (
        <BulkOrderSheet item={selected} onClose={() => setSelected(null)} onSubmit={handleSubmit} />
      )}
    </Layout>
  );
}
