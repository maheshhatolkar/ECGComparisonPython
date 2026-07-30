let currentApiUrl = "http://192.168.1.9:8000";

export const getApiUrl = () => currentApiUrl;

export const setApiUrl = (url) => {
  if (url && typeof url === 'string') {
    let cleaned = url.trim().replace(/\/+$/, '');
    if (!cleaned.startsWith('http://') && !cleaned.startsWith('https://')) {
      cleaned = 'http://' + cleaned;
    }
    currentApiUrl = cleaned;
  }
};
