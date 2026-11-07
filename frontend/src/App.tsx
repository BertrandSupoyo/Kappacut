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
import { AgentPage } from "./pages/Agent";
import { Account } from "./pages/Account";
import { Admin } from "./pages/Admin";
import { AdminOverview } from "./pages/admin/Overview";
import { AdminJobs } from "./pages/admin/Jobs";
import { AdminUsers } from "./pages/admin/Users";
import { AdminInvites } from "./pages/admin/Invites";
import { AdminRemovals } from "./pages/admin/Removals";
import { AdminTraining } from "./pages/AdminTraining";
import { AdminLabel } from "./pages/AdminLabel";
import { Editor } from "./pages/Editor";
import { NotFound } from "./pages/NotFound";
import { Copyright, Privacy, Terms } from "./pages/Legal";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/terms" element={<Terms />} />
      <Route path="/privacy" element={<Privacy />} />
      <Route path="/copyright" element={<Copyright />} />
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
        path="/app/agent"
        element={
          <RequireVerified>
            <AppShell>
              <AgentPage />
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
      <Route
        path="/admin"
        element={
          <RequireVerified>
            <AppShell>
              <Admin />
            </AppShell>
          </RequireVerified>
        }
      >
        <Route index element={<AdminOverview />} />
        <Route path="jobs" element={<AdminJobs />} />
        <Route path="users" element={<AdminUsers />} />
        <Route path="invites" element={<AdminInvites />} />
        <Route path="removals" element={<AdminRemovals />} />
        <Route path="training" element={<AdminTraining />} />
      </Route>
      <Route
        path="/admin/training/:id"
        element={
          <RequireVerified>
            <AppShell>
              <AdminLabel />
            </AppShell>
          </RequireVerified>
        }
      />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
