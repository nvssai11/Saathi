import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../context/AuthContext";
import { buyerApi, PaymentTerms } from "../../api/client";
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

const CATEGORY_KEY: Record<string, string> = {
  Bags: "categoryBags",
  Apparel: "categoryApparel",
  "Home Décor": "categoryHomeDecor",
};
const PRICE_BUCKET_KEY: Record<string, string> = {
  under50: "priceUnder50",
  "50to100": "price50to100",
  over100: "priceOver100",
};
const SORT_OPTION_KEY: Record<SortId, string> = {
  relevance: "sortRelevance",
  "price-asc": "sortPriceAsc",
  "price-desc": "sortPriceDesc",
  "discount-desc": "sortDiscountDesc",
};

export default function PlaceOrder() {
  const { t } = useTranslation();
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

  async function handleSubmit(args: {
    totalQty: number;
    qualityMin: number;
    deadline: string;
    buyerRef: string;
    paymentTerms: PaymentTerms;
  }) {
    if (!token || !selected) return;
    const res = await buyerApi.placeOrder(token, {
      buyer_ref: args.buyerRef,
      product_type: selected.productType,
      total_qty: args.totalQty,
      quality_min: args.qualityMin,
      deadline: args.deadline,
      payment_terms: args.paymentTerms,
    });
    navigate(`/buyer/orders/${res.order_id}`);
  }

  const searchSlot = (
    <div className="search-bar">
      <SearchIcon />
      <input
        aria-label={t("shop.searchLabel")}
        placeholder={t("shop.searchPlaceholder")}
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
            <h1>{t("shop.heroTitle")}</h1>
            <p>{t("shop.heroSubtitle")}</p>
          </div>
          <div className="promo-stats">
            <div className="promo-stat">
              <span className="promo-stat-icon">
                <ShieldCheckIcon />
              </span>
              {t("shop.statTrust")}
            </div>
            <div className="promo-stat">
              <span className="promo-stat-icon">
                <LayersIcon />
              </span>
              {t("shop.statMoq")}
            </div>
            <div className="promo-stat">
              <span className="promo-stat-icon">
                <HandshakeIcon />
              </span>
              {t("shop.statSupplier")}
            </div>
          </div>
        </div>

        <div className="shop-layout">
          <aside className="filter-panel">
            <div className="filter-panel-header">
              <h2 className="filter-title">{t("shop.filters")}</h2>
              {filtersActive && (
                <button type="button" className="filter-clear" onClick={clearFilters}>
                  {t("shop.clearAll")}
                </button>
              )}
            </div>
            <div className="filter-sections">
              <div className="filter-section">
                <h3 className="filter-title">{t("shop.category")}</h3>
                {CATEGORIES.map((c) => (
                  <label className="filter-check-row" key={c}>
                    <input
                      type="checkbox"
                      checked={categories.has(c)}
                      onChange={() => toggleCategory(c)}
                    />
                    {t(`shop.${CATEGORY_KEY[c] ?? c}`, c)}
                  </label>
                ))}
              </div>
              <div className="filter-section">
                <h3 className="filter-title">{t("shop.price")}</h3>
                {PRICE_BUCKETS.map((b) => (
                  <label className="filter-check-row" key={b.id}>
                    <input
                      type="checkbox"
                      checked={priceBuckets.has(b.id)}
                      onChange={() => togglePriceBucket(b.id)}
                    />
                    {t(`shop.${PRICE_BUCKET_KEY[b.id] ?? b.id}`, b.label)}
                  </label>
                ))}
              </div>
            </div>
          </aside>

          <div className="shop-results">
            <div className="results-toolbar">
              <span className="results-count">{t("shop.resultsCount", { count: items.length })}</span>
              <select
                className="sort-select"
                aria-label={t("shop.sortLabel")}
                value={sort}
                onChange={(e) => setSort(e.target.value as SortId)}
              >
                {SORT_OPTIONS.map((opt) => (
                  <option key={opt.id} value={opt.id}>
                    {t(`shop.${SORT_OPTION_KEY[opt.id]}`, opt.label)}
                  </option>
                ))}
              </select>
            </div>

            {items.length === 0 ? (
              <div className="card empty-state">
                <p>{filtersActive ? t("shop.noProductsWithFilters") : t("shop.noProducts")}</p>
                {filtersActive && (
                  <button type="button" className="btn btn-secondary" onClick={clearFilters}>
                    {t("shop.clearFiltersBtn")}
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
                        <span className="discount-badge">{t("shop.percentOffBadge", { pct })}</span>
                      </div>
                      <div className="product-info">
                        <span className="product-category">
                          {t(`shop.${CATEGORY_KEY[item.category] ?? item.category}`, item.category)}
                        </span>
                        <span className="product-name">{item.name}</span>
                        <div className="product-price-row">
                          <span className="product-price">₹{item.price}</span>
                          <span className="product-mrp">₹{item.factoryPrice}</span>
                          <span className="product-off">{t("shop.percentOff", { pct })}</span>
                        </div>
                        <div className="product-bottom-row">
                          <span className="verified-badge">✓ {t("shop.verified")}</span>
                          <span className="product-moq">{t("shop.moq", { moq: item.moq })}</span>
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
