import axios, { AxiosHeaders, AxiosError, type AxiosRequestConfig } from "axios";

import webConfig from "@/constants/common-env";
import {clearStoredAuthSession, getStoredAuthKey} from "@/store/auth";

type RequestConfig = AxiosRequestConfig & {
    redirectOnUnauthorized?: boolean;
};

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

const request = axios.create({
    baseURL: webConfig.apiUrl.replace(/\/$/, ""),
});

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
    nextConfig.headers = AxiosHeaders.from(createAuthorizationHeaders(nextConfig.headers, authKey));
    return nextConfig;
});

request.interceptors.response.use(
    (response) => response,
    async (error: AxiosError<ErrorPayload>) => {
        const status = error.response?.status;
        const shouldRedirect = (error.config as RequestConfig | undefined)?.redirectOnUnauthorized !== false;
        if (status === 401 && shouldRedirect && typeof window !== "undefined") {
            // Avoid redirect loop — only redirect if not already on /login
            const publicPath = ["/login", "/register", "/setup"].some((path) =>
                window.location.pathname.startsWith(path),
            );
            if (!publicPath) {
                await clearStoredAuthSession();
                window.location.replace("/login");
                // Return a never-resolving promise to prevent further error handling
                // while the browser navigates away
                return new Promise(() => {});
            }
        }

        const payload = error.response?.data;
        const message =
            errorMessageFromValue(payload?.detail) ||
            errorMessageFromValue(payload?.error) ||
            payload?.message ||
            error.message ||
            `请求失败 (${status || 500})`;
        return Promise.reject(new Error(message));
    },
);

type RequestOptions = {
    method?: string;
    body?: unknown;
    headers?: Record<string, string>;
    redirectOnUnauthorized?: boolean;
};

export async function httpRequest<T>(path: string, options: RequestOptions = {}) {
    const {method = "GET", body, headers, redirectOnUnauthorized = true} = options;
    const config: RequestConfig = {
        url: path,
        method,
        data: body,
        headers,
        redirectOnUnauthorized,
    };
    const response = await request.request<T>(config);
    return response.data;
}

export async function httpBlobRequest(path: string, options: Omit<RequestOptions, "body"> = {}) {
    const {method = "GET", headers, redirectOnUnauthorized = true} = options;
    const config: RequestConfig = {
        url: path,
        method,
        headers,
        redirectOnUnauthorized,
        responseType: "blob",
    };
    const response = await request.request<Blob>(config);
    return response.data;
}
