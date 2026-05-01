function getDefaultApiUrl() {
    if (process.env.NEXT_PUBLIC_API_URL) {
        return process.env.NEXT_PUBLIC_API_URL
    }
    if (process.env.NODE_ENV !== 'development') {
        return ''
    }
    const browserLocation = typeof window !== 'undefined' ? window.location : undefined
    if (browserLocation?.hostname) {
        return `${browserLocation.protocol}//${browserLocation.hostname}:8000`
    }
    return 'http://127.0.0.1:8000'
}

const webConfig = {
    apiUrl: getDefaultApiUrl(),
    appVersion: process.env.NEXT_PUBLIC_APP_VERSION || '0.0.0',
}

export default webConfig
