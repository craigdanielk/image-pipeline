import { useState, type FormEvent } from "react";
import { useAuth, type AuthResult } from "../AuthContext";

type Mode = "signin" | "signup";

export default function AuthPage() {
  const { signIn, signUp } = useAuth();
  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [feedback, setFeedback] = useState<{
    kind: "info" | "error";
    text: string;
  } | null>(null);
  const [busy, setBusy] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setFeedback(null);
    setBusy(true);

    let result: AuthResult;
    if (mode === "signin") {
      result = await signIn(email, password);
    } else {
      result = await signUp(email, password);
    }

    setBusy(false);

    if (result.ok) {
      if (mode === "signup") {
        setFeedback({
          kind: "info",
          text: "Check your email for a confirmation link.",
        });
        setMode("signin");
      }
      // sign-in success: the AuthProvider state change will unmount this page
      // via ProtectedRoute — no explicit navigation needed.
    } else {
      setFeedback({ kind: "error", text: result.error });
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-card glass-panel">
        <h1 className="auth-card__title">
          {mode === "signin" ? "Sign in" : "Create account"}
        </h1>
        <p className="auth-card__subtitle">
          {mode === "signin"
            ? "Welcome back — sign in to continue."
            : "Register for a new account."}
        </p>

        <form className="auth-card__form" onSubmit={handleSubmit}>
          <label className="field-group">
            <span className="field-label">Email</span>
            <input
              className="field-input"
              type="email"
              required
              autoComplete={mode === "signin" ? "email" : "new-email"}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
            />
          </label>

          <label className="field-group">
            <span className="field-label">Password</span>
            <input
              className="field-input"
              type="password"
              required
              minLength={6}
              autoComplete={mode === "signin" ? "current-password" : "new-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="········"
            />
          </label>

          {feedback && (
            <p
              className={`auth-card__feedback ${
                feedback.kind === "error"
                  ? "auth-card__feedback--error"
                  : "auth-card__feedback--info"
              }`}
            >
              {feedback.text}
            </p>
          )}

          <button
            className="btn btn--primary-solid auth-card__submit"
            type="submit"
            disabled={busy}
          >
            {busy
              ? "Please wait…"
              : mode === "signin"
              ? "Sign in"
              : "Create account"}
          </button>
        </form>

        <p className="auth-card__switch">
          {mode === "signin" ? (
            <>
              No account?{" "}
              <button
                className="btn-link"
                onClick={() => {
                  setMode("signup");
                  setFeedback(null);
                }}
              >
                Create one
              </button>
            </>
          ) : (
            <>
              Already registered?{" "}
              <button
                className="btn-link"
                onClick={() => {
                  setMode("signin");
                  setFeedback(null);
                }}
              >
                Sign in
              </button>
            </>
          )}
        </p>
      </div>
    </div>
  );
}
