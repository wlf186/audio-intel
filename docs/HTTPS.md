# Local HTTPS and Browser Microphone Access

HTTP is the default and is sufficient for access through `localhost`. Browsers usually require a secure context before granting microphone access to a page opened through a LAN IP. Sandevistan Audio can create a project-specific local CA and server certificate with an already-installed `mkcert` binary.

The helper does not run `mkcert -install`, does not modify the host trust store, and does not access the network. CA material is stored under the configured data directory.

## Enable project-managed HTTPS

Linux:

```bash
./service.sh tls enable
./service.sh start all
```

Native Windows:

```powershell
.\service.cmd tls enable
.\service.cmd start all
```

`tls enable` adds localhost, the machine hostname, and usable addresses from active network adapters to the certificate. Append a host that cannot be discovered inside a container or VPN namespace:

```bash
./service.sh tls enable --host 192.168.1.20
```

```powershell
.\service.cmd tls enable --host 192.168.1.20
```

The saved mode lives at `<AUDIO_INTEL_DATA_DIR>/tls/service-profile.json`. Later `start`, `restart`, and Linux `run` commands reuse it without requiring exported TLS environment variables. If `AUDIO_INTEL_DATA_DIR` is overridden, use the same value for enablement and later lifecycle commands.

Enabling or disabling HTTPS saves the next-start configuration and does not interrupt active jobs. Apply it immediately to a background service with `--restart`:

```bash
./service.sh tls enable --restart
./service.sh tls disable --restart
```

Both commands perform a complete `restart all`. Disabling HTTPS preserves the CA and server certificate.

## Inspect the active protocol

```bash
./service.sh tls status
./service.sh status
./service.sh tls fingerprint
```

Use `service.cmd` on Windows. `status` reads the actual API process and warns if its protocol differs from the saved next-start profile.

HTTPS mode makes port 20810 HTTPS-only. It does not serve HTTP on the same port and does not redirect HTTP requests. A browser `ERR_SSL_PROTOCOL_ERROR` commonly means the URL and actual service mode do not match.

## Trust the project root CA

The login page and global “HTTPS certificate” action expose the configured public root certificate and SHA-256 fingerprint before authentication. If the browser cannot open the first self-signed connection, transfer the public file `data/tls/audio-intel-root-ca.cer` through a trusted channel.

Always compare its fingerprint with the service host through a separate trusted channel:

```bash
./service.sh tls fingerprint
```

### Windows clients

Open `sandevistan-audio-root-ca.cer` and install it into **Trusted Root Certification Authorities** for the current user or local computer, then restart Chrome or Edge. Administrators can also run:

```powershell
certutil -addstore -f Root <certificate-path>
```

### iOS clients

Open the `.cer` file to install the profile, then enable full trust in **Settings → General → About → Certificate Trust Settings** and restart the browser.

### Temporary browser bypass

Desktop Chrome or Edge may allow **Advanced → Continue** on the certificate warning page. The connection is encrypted, but the server identity remains unverified and an active intermediary could still capture the API key or audio. Do not rely on this path for Safari, iOS, Firefox, or future browser behavior.

## Renew addresses and certificates

Run `tls enable` again after the machine's address changes. Missing SANs or a near-expiry leaf certificate cause only the server certificate to be replaced; the project root CA is preserved so already-trusted clients do not need to reinstall it.

Lower-level `tls create`, `tls renew`, and `tls fingerprint` commands remain available. `create` and `renew` do not replace `enable`: only `enable` stores HTTPS as the service mode.

## External certificates and environment overrides

`service.sh` and `service.cmd` do not load a general `.env` automatically. Explicit values override the saved project profile and are intended for reverse proxies or externally managed certificates:

```bash
export AUDIO_INTEL_PROTOCOL=https
export AUDIO_INTEL_TLS_CERT_FILE=/path/to/server.pem
export AUDIO_INTEL_TLS_KEY_FILE=/path/to/server-key.pem
export AUDIO_INTEL_TLS_CA_FILE=/path/to/public-ca.pem
./service.sh start all
```

Startup and restart validate the protocol, certificate, and key before stopping a currently running service. HTTP mode rejects configured TLS files to avoid a misleading partially configured state.

## Security boundaries

These files are private and must never be distributed or committed:

- `data/tls/ca/rootCA-key.pem`
- `data/tls/server-key.pem`

The `.cer` and public CA PEM files contain no private key and are intended for distribution after fingerprint verification. If the root private key leaks, replace the CA and reinstall the new public certificate on every client.

The public TLS bootstrap endpoints expose only the fixed configured public CA certificate and its fingerprint. They do not expose private keys, detailed system information, tasks, media, or results.

Project-managed HTTPS is suitable for a controlled LAN. Do not use it as a substitute for a publicly trusted certificate when exposing the service to the internet, and always enable `AUDIO_INTEL_API_KEY` outside a trusted network.
