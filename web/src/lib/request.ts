import axios, { AxiosHeaders, AxiosError, type AxiosRequestConfig } from "axios";

import webConfig from "@/constants/common-env";
import {clearStoredAuthSessionIfCurrent, getStoredAuthKey, getStoredAuthSession} from "@/store/auth";

type RequestConfig = AxiosRequestConfig & {
    redirectOnUnauthorized?: boolean;
    authKeyUsed?: string;
};

const AUTH_SETUP_CHECK_TIMEOUT_MS = 3000;
const AUTH_SETUP_STATUS_PATH = "/api/setup/status";

type ErrorPayload = {
    detail?: string | { error?: string | { message?: string } };
    error?: string | { message?: string };
    message?: string;
};

function errorMessageFromValue(value: unknown): string {
    if (typeof value === "string") {
        return value;
    }
    if (!value || typeof value !== "object") {
        return "";
    }

    const item = value as { error?: unknown; message?: unknown };
    if (typeof item.message === "string") {
        return item.message;
    }
    return errorMessageFromValue(item.error);
}

function normalizedTimeoutMs(timeoutMs: number | undefined) {
    if (typeof timeoutMs !== "number" || !Number.isFinite(timeoutMs) || timeoutMs < 0) {
        return undefined;
    }
    return timeoutMs;
}

function resolveRequestTimeoutMs(path: string, timeoutMs: number | undefined) {
    const explicitTimeoutMs = normalizedTimeoutMs(timeoutMs);
    if (explicitTimeoutMs !== undefined) {
        return explicitTimeoutMs;
    }

    const requestPath = path.split("?")[0];
    if (requestPath === AUTH_SETUP_STATUS_PATH) {
        return AUTH_SETUP_CHECK_TIMEOUT_MS;
    }
    return undefined;
}

function authorizationTokenFromHeaders(headers: Record<string, string | number | boolean>) {
    const value = Object.entries(headers).find(([key]) => key.toLowerCase() === "authorization")?.[1];
    const text = String(value || "").trim();
    return text.toLowerCase().startsWith("bearer ") ? text.slice(7).trim() : "";
}

const request = axios.create({
    baseURL: webConfig.apiUrl.replace(/\/$/, ""),
});

export async function shouldRedirectAfterUnauthorizedAuthFailure(
    authKeyUsed: string,
    deps: {
        clearCurrentSession: (expectedKey: string) => Promise<boolean>;
        getSession: () => Promise<unknown>;
    },
) {
    const token = String(authKeyUsed || "").trim();
    if (token) {
        return deps.clearCurrentSession(token);
    }
    return !(await deps.getSession());
}

export function createAuthorizationHeaders(headers: AxiosRequestConfig["headers"] | undefined, authKey: string) {
    const source =
        headers instanceof AxiosHeaders
            ? headers.toJSON()
            : ({ ...(headers || {}) } as Record<string, string | number | boolean>);
    const nextHeaders = { ...source } as Record<string, string | number | boolean>;
    const hasAuthorization = Object.entries(nextHeaders).some(
        ([key, value]) => key.toLowerCase() === "authorization" && String(value || "").trim(),
    );
    const token = String(authKey || "").trim();
    if (token && !hasAuthorization) {
        nextHeaders.Authorization = `Bearer ${token}`;
    }
    return nextHeaders;
}

request.interceptors.request.use(async (config) => {
    const nextConfig = {...config};
    const authKey = await getStoredAuthKey();
    const nextHeaders = createAuthorizationHeaders(nextConfig.headers, authKey);
    nextConfig.headers = AxiosHeaders.from(nextHeaders);
    const authKeyUsed = authorizationTokenFromHeaders(nextHeaders);
    if (authKeyUsed) {
        (nextConfig as RequestConfig).authKeyUsed = authKeyUsed;
    }
    return nextConfig;
});

request.interceptors.response.use(
    (response) => response,
    async (error: AxiosError<ErrorPayload>) => {
        const status = error.response?.status;
        const payload = error.response?.data;
        const message =
            errorMessageFromValue(payload?.detail) ||
            errorMessageFromValue(payload?.error) ||
            payload?.message ||
            error.message ||
            `请求失败 (${status || 500})`;
        const shouldRedirect = (error.config as RequestConfig | undefined)?.redirectOnUnauthorized !== false;
        if (status === 401 && shouldRedirect && typeof window !== "undefined") {
            // Avoid redirect loop — only redirect if not already on /login
            const publicPath = ["/login", "/register", "/setup"].some((path) =>
                window.location.pathname.startsWith(path),
            );
            if (!publicPath) {
                const authKeyUsed = String((error.config as RequestConfig | undefined)?.authKeyUsed || "").trim();
                const shouldRedirect = await shouldRedirectAfterUnauthorizedAuthFailure(authKeyUsed, {
                    clearCurrentSession: clearStoredAuthSessionIfCurrent,
                    getSession: getStoredAuthSession,
                });
                if (shouldRedirect) {
                    window.location.replace("/login");
                }
            }
        }

        return Promise.reject(new Error(message));
    },
);

type RequestOptions = {
    method?: string;
    body?: unknown;
    headers?: Record<string, string>;
    redirectOnUnauthorized?: boolean;
    timeoutMs?: number;
};

export async function httpRequest<T>(path: string, options: RequestOptions = {}) {
    const {method = "GET", body, headers, redirectOnUnauthorized = true, timeoutMs} = options;
    const config: RequestConfig = {
        url: path,
        method,
        data: body,
        headers,
        redirectOnUnauthorized,
    };
    const timeout = resolveRequestTimeoutMs(path, timeoutMs);
    if (timeout !== undefined) {
        config.timeout = timeout;
    }
    const response = await request.request<T>(config);
    return response.data;
}

export async function httpBlobRequest(path: string, options: Omit<RequestOptions, "body"> = {}) {
    const {method = "GET", headers, redirectOnUnauthorized = true, timeoutMs} = options;
    const config: RequestConfig = {
        url: path,
        method,
        headers,
        redirectOnUnauthorized,
        responseType: "blob",
    };
    const timeout = resolveRequestTimeoutMs(path, timeoutMs);
    if (timeout !== undefined) {
        config.timeout = timeout;
    }
    const response = await request.request<Blob>(config);
    return response.data;
}
