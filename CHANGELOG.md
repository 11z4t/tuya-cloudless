# Changelog

All notable changes to Tuya Cloudless are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added
- Repo scaffolding, CI/CD pipeline (lint/typecheck/test/secrets-scan)
- Custom component skeleton (config flow, coordinator, entity base, strings EN+SV)
- Tuya LAN protocol parser v3.1–3.5 (frame encode/decode, DPS handling)
- Crypto module — AES-ECB (v3.1–3.3) + AES-GCM (v3.4–3.5) + ECDH session key
- UDP discovery listener (port 6666/6667, async, automatic device detection)
