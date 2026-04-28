function getDefaultApiUrl() {
    if (process.env.NEXT_PUBLIC_API_URL) {
        return process.env.NEXT_PUBLIC_API_URL
    }
    if (process.env.NODE_ENV !== 'development') {
        return ''
    }
    if (typeof window !== 'undefined' && window.location.hostname) {
        return `${window.location.protocol}//${window.location.hostname}:8000`
    }
    return 'http://127.0.0.1:8000'
}

const webConfig = {
    apiUrl: getDefaultApiUrl(),
    appVersion: process.env.NEXT_PUBLIC_APP_VERSION || '0.0.0',
}

export default webConfig
