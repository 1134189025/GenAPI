export type PromoSearchParams = {
  get(name: string): string | null;
};

export function resolvePromoQueryParam(searchParams: PromoSearchParams | null | undefined) {
  return searchParams?.get("promo")?.trim() || "";
}
