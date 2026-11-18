// AstrBotPluginPage 的 NoneBot2 实现。
//
// 上游页面（pages/）通过 window.AstrBotPluginPage 访问宿主：
//   ready / getContext / apiGet / apiPost / upload / download
//   subscribeSSE / unsubscribeSSE
//
// 本脚本在 NoneBot2 下提供等价实现：所有请求打到 /meme_manager/api/*，
// 并自动附加 Bearer token（与上游一致），从而让前端零改动运行。
(function () {
  "use strict";

  const PREFIX = "/meme_manager";
  const TOKEN_KEY = "meme_manager_nb2_token";
  const DEFAULT_TIMEOUT = 120000;

  function baseUrl() {
    const current = new URL(window.location.href);
    return `${current.origin}${PREFIX}`;
  }

  function readToken() {
    try {
      return window.localStorage.getItem(TOKEN_KEY) || "";
    } catch (error) {
      return "";
    }
  }

  function writeToken(token) {
    try {
      if (token) window.localStorage.setItem(TOKEN_KEY, token);
      else window.localStorage.removeItem(TOKEN_KEY);
    } catch (error) {
      /* 隐私模式下忽略 */
    }
  }

  function buildQuery(params) {
    const query = new URLSearchParams();
    Object.entries(params || {}).forEach(([key, value]) => {
      if (value === undefined || value === null || value === "") return;
      if (Array.isArray(value)) {
        value.forEach((item) => query.append(key, String(item)));
      } else {
        query.append(key, String(value));
      }
    });
    return query.toString();
  }

  function currentAssetToken() {
    return new URLSearchParams(window.location.search).get("asset_token") || "";
  }

  async function parseResponse(response) {
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      return response.json();
    }
    if (contentType.startsWith("image/") || contentType.includes("octet-stream")) {
      return response.blob();
    }
    const text = await response.text();
    if (!text) return null;
    try {
      return JSON.parse(text);
    } catch (error) {
      return text;
    }
  }

  async function request(path, { method = "GET", body, headers, raw = false } = {}) {
    const url = new URL(`${baseUrl()}/api/${String(path).replace(/^\/+/, "")}`);
    const assetToken = currentAssetToken();
    if (assetToken && !url.searchParams.has("asset_token")) {
      url.searchParams.set("asset_token", assetToken);
    }
    const finalHeaders = Object.assign({ Accept: "application/json" }, headers || {});
    const token = readToken();
    if (token) finalHeaders.Authorization = `Bearer ${token}`;
    if (body !== undefined && !(body instanceof FormData) && !(body instanceof Blob)) {
      finalHeaders["Content-Type"] = "application/json";
    }

    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), DEFAULT_TIMEOUT);
    try {
      const response = await fetch(url.toString(), {
        method,
        headers: finalHeaders,
        body:
          body === undefined
            ? undefined
            : body instanceof FormData || body instanceof Blob
              ? body
              : JSON.stringify(body),
        signal: controller.signal,
        credentials: "same-origin",
      });
      if (!response.ok) {
        let detail = null;
        try {
          detail = await parseResponse(response);
        } catch (error) {
          detail = null;
        }
        const message =
          (detail && (detail.message || detail.detail)) ||
          `请求失败（HTTP ${response.status}）`;
        const failure = new Error(String(message));
        failure.status = response.status;
        failure.payload = detail;
        throw failure;
      }
      return raw ? response : await parseResponse(response);
    } finally {
      window.clearTimeout(timer);
    }
  }

  const pageApi = {
    async ready() {
      return true;
    },

    getContext() {
      const params = new URLSearchParams(window.location.search);
      return {
        prefix: PREFIX,
        assetToken: currentAssetToken(),
        managedPackId: params.get("managed_pack_id") || "",
        view: params.get("view") || "",
      };
    },

    async apiGet(endpoint, params) {
      const query = buildQuery(params);
      const path = query ? `${endpoint}${endpoint.includes("?") ? "&" : "?"}${query}` : endpoint;
      return request(path, { method: "GET" });
    },

    async apiPost(endpoint, body) {
      return request(endpoint, { method: "POST", body: body === undefined ? {} : body });
    },

    async apiDelete(endpoint, body) {
      return request(endpoint, { method: "DELETE", body });
    },

    async upload(endpoint, blob, filename) {
      const form = new FormData();
      form.append("file", blob, filename || "upload.bin");
      return request(endpoint, { method: "POST", body: form });
    },

    async download(endpoint, params, filename) {
      const query = buildQuery(params);
      const path = query ? `${endpoint}${endpoint.includes("?") ? "&" : "?"}${query}` : endpoint;
      const response = await request(path, { method: "GET", raw: true });
      const blob = await response.blob();
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = filename || "download.bin";
      document.body.append(link);
      link.click();
      window.setTimeout(() => {
        URL.revokeObjectURL(link.href);
        link.remove();
      }, 0);
      return blob;
    },

    subscribeSSE(endpoint, handler) {
      const url = new URL(`${baseUrl()}/api/${String(endpoint).replace(/^\/+/, "")}`);
      const token = readToken();
      const source = new EventSource(url.toString());
      const listener = (event) => {
        if (typeof handler === "function") {
          try {
            handler(JSON.parse(event.data));
          } catch (error) {
            handler(event.data);
          }
        }
      };
      source.onmessage = listener;
      source.onerror = () => {
        if (typeof handler === "function") handler({ type: "error" });
      };
      return source;
    },

    unsubscribeSSE(handle) {
      if (handle && typeof handle.close === "function") handle.close();
    },

    async ensureToken() {
      let token = readToken();
      if (token) return token;
      try {
        const payload = await request("bridge/auth_token");
        token = String((payload && payload.token) || "").trim();
      } catch (error) {
        token = "";
      }
      if (!token) {
        // 无上游 token 时使用 asset_token（同一来源校验）。
        token = currentAssetToken();
      }
      if (token) writeToken(token);
      return token;
    },
  };

  window.AstrBotPluginPage = pageApi;
  window.MemeManagerNB2 = pageApi;
})();
