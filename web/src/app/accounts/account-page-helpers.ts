"use client";

import {
  getAccountOperationRefs,
  type Account,
  type AccountOperationRef,
  type AccountStatus,
  type AccountType,
} from "@/lib/api";

export type AccountStatusFilter = AccountStatus | "all" | "problem";

export function isProblemAccount(account: Account) {
  if (account.status === "禁用") {
    return false;
  }
  if (account.status === "限流" || account.status === "异常") {
    return true;
  }
  return account.status === "正常" && account.imageQuotaUnknown === false && account.quota <= 0;
}

export function filterAccountsForAccountsPage(
  accounts: Account[],
  filters: {
    query?: string;
    typeFilter?: AccountType | "all";
    statusFilter?: AccountStatusFilter;
  },
) {
  const normalizedQuery = (filters.query ?? "").trim().toLowerCase();
  const typeFilter = filters.typeFilter ?? "all";
  const statusFilter = filters.statusFilter ?? "all";

  return accounts.filter((account) => {
    const searchMatched =
      normalizedQuery.length === 0 || (account.email ?? "").toLowerCase().includes(normalizedQuery);
    const typeMatched = typeFilter === "all" || account.type === typeFilter;
    const statusMatched =
      statusFilter === "all" ||
      (statusFilter === "problem" ? isProblemAccount(account) : account.status === statusFilter);
    return searchMatched && typeMatched && statusMatched;
  });
}

export function getAccountRefreshRefsForFilter(
  accounts: Account[],
  filters: {
    query?: string;
    typeFilter?: AccountType | "all";
    statusFilter?: AccountStatusFilter;
  },
) {
  return getAccountOperationRefs(filterAccountsForAccountsPage(accounts, filters));
}

export function getProblemAccountRefsForFilter(
  accounts: Account[],
  filters: {
    query?: string;
    typeFilter?: AccountType | "all";
    statusFilter?: AccountStatusFilter;
  },
) {
  return getAccountOperationRefs(filterAccountsForAccountsPage(accounts, filters).filter(isProblemAccount));
}

export function getDisplayAccountReference(account: Account) {
  const tokenRef = String(account.token_ref || "").trim();
  if (tokenRef) return tokenRef;
  const id = String(account.id || "").trim();
  return id ? `id:${id}` : "—";
}

export function buildAccountReferenceExport(accounts: Account[]) {
  return `${accounts.map(getDisplayAccountReference).filter((item) => item !== "—").join("\n")}\n`;
}

export function confirmAccountDeletion(
  refs: AccountOperationRef[],
  actionLabel: string,
  confirm: (message: string) => boolean = (message) => window.confirm(message),
) {
  if (refs.length === 0) return false;
  return confirm(`${actionLabel}将删除 ${refs.length} 个账号，此操作不可恢复。确认继续？`);
}

export function parseAccountQuotaInput(value: string): { ok: true; quota: number } | { ok: false; message: string } {
  const normalized = value.trim();
  const quota = Number(normalized || 0);
  if (!Number.isFinite(quota)) {
    return { ok: false, message: "额度必须是有效数字" };
  }
  return { ok: true, quota };
}
