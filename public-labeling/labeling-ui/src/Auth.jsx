import { useState } from "react";
import { supabase } from "./supabase";
import "./Auth.css";

// mode: "signin" | "signup" | "forgot" | "reset"
export default function Auth({ initialMode = "signin", onDone }) {
  const [mode, setMode] = useState(initialMode);
  const [fullName, setFullName] = useState("");
  const [organization, setOrganization] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const switchTo = (m) => { setMode(m); setError(""); setNotice(""); };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (mode === "signin") {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) throw error;
      } else if (mode === "signup") {
        const { data, error } = await supabase.auth.signUp({
          email,
          password,
          options: {
            data: { full_name: fullName.trim(), organization: organization.trim() },
            emailRedirectTo: window.location.origin,
          },
        });
        if (error) throw error;
        // With "Confirm email" on, there's no session until they click the link.
        if (!data.session) setNotice("Check your email for a link to confirm your account, then sign in.");
      } else if (mode === "forgot") {
        const { error } = await supabase.auth.resetPasswordForEmail(email, { redirectTo: window.location.origin });
        if (error) throw error;
        setNotice("If that email has an account, a reset link is on its way.");
      } else if (mode === "reset") {
        const { error } = await supabase.auth.updateUser({ password });
        if (error) throw error;
        onDone?.();
      }
    } catch (err) {
      setError(err.message || "Something went wrong. Try again.");
    } finally {
      setBusy(false);
    }
  };

  const titles = {
    signin: "Sign in to start labeling",
    signup: "Create a volunteer account",
    forgot: "Reset your password",
    reset: "Choose a new password",
  };

  return (
    <div className="auth-screen">
      <form className="auth-card" onSubmit={handleSubmit}>
        <div className="auth-brand">🦦 Otter Labeling</div>
        <h1>{titles[mode]}</h1>
        {mode === "signup" && (
          <>
            <label>
              Full name
              <input value={fullName} onChange={(e) => setFullName(e.target.value)} required autoComplete="name" />
            </label>
          </>
        )}

        {mode !== "reset" && (
          <label>
            Email
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" />
          </label>
        )}

        {mode !== "forgot" && (
          <label>
            {mode === "reset" ? "New password" : "Password"}
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={mode === "signin" ? undefined : 8}
              autoComplete={mode === "signin" ? "current-password" : "new-password"}
            />
          </label>
        )}

        {error && <p className="auth-error">{error}</p>}
        {notice && <p className="auth-notice">{notice}</p>}

        <button className="btn-submit auth-btn" type="submit" disabled={busy}>
          {busy ? "One moment…" : {
            signin: "Sign in", signup: "Create account", forgot: "Send reset link", reset: "Save password",
          }[mode]}
        </button>

        <div className="auth-links">
          {mode === "signin" && (
            <>
              <button type="button" onClick={() => switchTo("signup")}>New here? Create an account</button>
              <button type="button" onClick={() => switchTo("forgot")}>Forgot password?</button>
            </>
          )}
          {(mode === "signup" || mode === "forgot") && (
            <button type="button" onClick={() => switchTo("signin")}>Already have an account? Sign in</button>
          )}
        </div>
      </form>
    </div>
  );
}
