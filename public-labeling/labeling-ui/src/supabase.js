import { createClient } from "@supabase/supabase-js";
import axios from "axios";

export const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL;
const PUBLISHABLE_KEY = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY;

if (!SUPABASE_URL || !PUBLISHABLE_KEY) {
  // Shows up in the console instead of a mysterious blank page.
  console.error("Set VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY (see labeling-ui/.env.example)");
}

// The browser only uses Supabase for Auth. All table reads/writes go through
// the FastAPI backend, which checks the access token we attach below.
export const supabase = createClient(SUPABASE_URL, PUBLISHABLE_KEY);

export function frameUrl(storagePath) {
  return `${SUPABASE_URL}/storage/v1/object/public/frames/${storagePath}`;
}

// axios instance that always carries the current user's access token.
export const api = axios.create();
api.interceptors.request.use(async (config) => {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});
