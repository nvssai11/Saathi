export interface CatalogItem {
  productType: string;
  name: string;
  category: string;
  emoji: string;
  price: number;
  factoryPrice: number;
  moq: number;
  swatch: string;
}

export const CATALOG: CatalogItem[] = [
  { productType: "jute-tote-bag", name: "Jute Tote Bag", category: "Bags", emoji: "🛍️", price: 42, factoryPrice: 64, moq: 100, swatch: "linear-gradient(135deg,#c9a15a,#8a6a30)" },
  { productType: "cotton-tote-bag", name: "Cotton Canvas Bag", category: "Bags", emoji: "👜", price: 38, factoryPrice: 55, moq: 100, swatch: "linear-gradient(135deg,#7a1fa8,#4f1570)" },
  { productType: "khadi-scarf", name: "Khadi Cotton Scarf", category: "Apparel", emoji: "🧣", price: 65, factoryPrice: 98, moq: 50, swatch: "linear-gradient(135deg,#ff9f1c,#e88600)" },
  { productType: "bamboo-basket", name: "Bamboo Storage Basket", category: "Home Décor", emoji: "🧺", price: 85, factoryPrice: 120, moq: 50, swatch: "linear-gradient(135deg,#5f8a3f,#3c5c26)" },
  { productType: "terracotta-pot", name: "Terracotta Planter Pot", category: "Home Décor", emoji: "🪴", price: 55, factoryPrice: 80, moq: 100, swatch: "linear-gradient(135deg,#c1542f,#8a3a1f)" },
  { productType: "block-print-cushion", name: "Block-Print Cushion Cover", category: "Home Décor", emoji: "🛋️", price: 48, factoryPrice: 72, moq: 100, swatch: "linear-gradient(135deg,#c1327a,#7a1f52)" },
  { productType: "handloom-stole", name: "Handloom Stole", category: "Apparel", emoji: "🧕", price: 120, factoryPrice: 175, moq: 50, swatch: "linear-gradient(135deg,#2f6f8a,#1c4a5c)" },
  { productType: "jute-door-mat", name: "Jute Door Mat", category: "Home Décor", emoji: "🚪", price: 30, factoryPrice: 46, moq: 150, swatch: "linear-gradient(135deg,#8a6a30,#5c4620)" },
];

export const CATEGORIES = ["Bags", "Apparel", "Home Décor"];

export interface PriceBucket {
  id: string;
  label: string;
  min: number;
  max: number;
}

export const PRICE_BUCKETS: PriceBucket[] = [
  { id: "under50", label: "Under ₹50/unit", min: 0, max: 50 },
  { id: "50to100", label: "₹50 – ₹100/unit", min: 50, max: 100 },
  { id: "over100", label: "Over ₹100/unit", min: 100, max: Infinity },
];

export const SORT_OPTIONS = [
  { id: "relevance", label: "Sort by: Relevance" },
  { id: "price-asc", label: "Price: Low to High" },
  { id: "price-desc", label: "Price: High to Low" },
  { id: "discount-desc", label: "Discount: High to Low" },
] as const;

export const QUALITY_TIERS = [
  { label: "Standard", value: 2, blurb: "Everyday use finish" },
  { label: "Premium", value: 4, blurb: "Tighter tolerances, finer finish" },
  { label: "Export Grade", value: 5, blurb: "Highest tier — export-ready" },
];

const QUALITY_TIER_MARKUP = 0.05;

function tierMultiplier(qualityValue: number): number {
  const tierIndex = QUALITY_TIERS.findIndex((t) => t.value === qualityValue);
  return Math.pow(1 + QUALITY_TIER_MARKUP, Math.max(tierIndex, 0));
}

export function priceForTier(item: CatalogItem, qualityValue: number): number {
  return Math.round(item.price * tierMultiplier(qualityValue));
}

export function factoryPriceForTier(item: CatalogItem, qualityValue: number): number {
  return Math.round(item.factoryPrice * tierMultiplier(qualityValue));
}

export function discountPct(item: CatalogItem): number {
  return Math.round(((item.factoryPrice - item.price) / item.factoryPrice) * 100);
}

const CATALOG_BY_TYPE = new Map(CATALOG.map((item) => [item.productType, item.name]));
const CATALOG_ITEM_BY_TYPE = new Map(CATALOG.map((item) => [item.productType, item]));

export function catalogItemFor(productType: string): CatalogItem | undefined {
  return CATALOG_ITEM_BY_TYPE.get(productType);
}

export function formatProductType(productType: string): string {
  const known = CATALOG_BY_TYPE.get(productType);
  if (known) return known;
  return productType
    .split("-")
    .filter(Boolean)
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(" ");
}
