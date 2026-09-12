import { Route, Routes } from "react-router-dom";
import { RequireVerified } from "./auth/guards";
import { AppShell } from "./components/AppShell";
import { Landing } from "./pages/Landing";
import { Login } from "./pages/Login";
import { Register } from "./pages/Register";
import { Verify } from "./pages/Verify";
import { Forgot } from "./pages/Forgot";
import { Reset } from "./pages/Reset";
import { Projects } from "./pages/Projects";
import { Account } from "./pages/Account";
import { Editor } from "./pages/Editor";
import { NotFound } from "./pages/NotFound";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route path="/verify" element={<Verify />} />
      <Route path="/forgot" element={<Forgot />} />
      <Route path="/reset" element={<Reset />} />
      <Route
        path="/app"
        element={
          <RequireVerified>
            <AppShell>
              <Projects />
            </AppShell>
          </RequireVerified>
        }
      />
      <Route
        path="/app/p/:id"
        element={
          <RequireVerified>
            <AppShell>
              <Editor />
            </AppShell>
          </RequireVerified>
        }
      />
      <Route
        path="/account"
        element={
          <RequireVerified>
            <AppShell>
              <Account />
            </AppShell>
          </RequireVerified>
        }
      />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
