import { readFileSync } from 'node:fs'
import { networkInterfaces } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { NextConfig } from 'next'

const projectRoot = join(dirname(fileURLToPath(import.meta.url)), '..')

function readAppVersion() {
    try {
        const version = readFileSync(join(projectRoot, 'VERSION'), 'utf-8').trim()
        return version || '0.0.0'
    } catch {
        return '0.0.0'
    }
}

const appVersion = process.env.NEXT_PUBLIC_APP_VERSION || readAppVersion()
const localDevOrigins = Object.values(networkInterfaces())
    .flatMap((items) => items || [])
    .filter((item) => item.family === 'IPv4')
    .map((item) => item.address)
const configuredDevOrigins = (process.env.NEXT_ALLOWED_DEV_ORIGINS || '')
    .split(',')
    .map((origin) => origin.trim())
    .filter(Boolean)

const allowedDevOrigins = [
    'localhost',
    '127.0.0.1',
    ...localDevOrigins,
    ...configuredDevOrigins,
]

const nextConfig: NextConfig = {
    allowedDevOrigins,
    env: {
        NEXT_PUBLIC_APP_VERSION: appVersion,
    },
    output: 'export',
    trailingSlash: true,
    images: {
        unoptimized: true,
    },
}

export default nextConfig
