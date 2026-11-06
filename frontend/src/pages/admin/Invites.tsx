import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { jdelete, jget, jpost } from "@/api/client";
import { useToast } from "@/components/Toast";
import { fmtAgo } from "@/lib/format";
import { LoadError, Loading, PageHead } from "./ui";

interface AdminInvite {
  id: string;
  email: string;
  registered: boolean;
  created_at: string;
}

export function AdminInvites() {
  const qc = useQueryClient();
  const toast = useToast();
  const [email, setEmail] = useState("");
  const query = useQuery({
    queryKey: ["admin", "invites"],
    queryFn: () => jget<{ invite_only: boolean; invites: AdminInvite[] }>("/api/admin/invites"),
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["admin", "invites"] });
  const add = useMutation({
    mutationFn: () => jpost<{ created: boolean }>("/api/admin/invites", { email: email.trim() }),
    onSuccess: (r) => {
      refresh();
      toast(r.created ? `Invited ${email.trim()}` : `${email.trim()} was already invited`);
      setEmail("");
    },
    onError: () => toast("That doesn't look like an email address.", "error"),
  });
  const remove = useMutation({
    mutationFn: (id: string) => jdelete(`/api/admin/invites/${id}`),
    onSuccess: refresh,
    onError: (e) => toast((e as Error).message || "Couldn't remove the invite.", "error"),
  });
  const data = query.data;

  return (
    <div className="admin-page">
      <PageHead
        title="Invites"
        note={data && (data.invite_only
          ? "Sign-up is invite-only: only these emails can register."
          : "Sign-up is open to anyone. Set INVITE_ONLY=true to limit it to these emails.")}
      />
      <section className="glass admin-card" aria-label="Invites">
        <form className="admin-filters" style={{ marginBottom: "var(--sp-3)" }} onSubmit={(e) => { e.preventDefault(); if (email.trim()) add.mutate(); }}>
          <input
            className="admin-input admin-search"
            type="email"
            aria-label="Email to invite"
            placeholder="name@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <button className="btn ghost small" disabled={!email.trim() || add.isPending}>Send invite</button>
        </form>
        {query.isPending ? (
          <Loading />
        ) : !data ? (
          <LoadError query={query} what="the invites" />
        ) : data.invites.length === 0 ? (
          <p className="muted admin-none">Nobody has been invited yet.</p>
        ) : (
          <div className="table-wrap">
            <table className="admin-table" style={{ minWidth: 480 }}>
              <thead>
                <tr><th>Email</th><th>Status</th><th>Invited</th><th /></tr>
              </thead>
              <tbody>
                {data.invites.map((i) => (
                  <tr key={i.id}>
                    <td className="cell-clip" title={i.email}>{i.email}</td>
                    <td>{i.registered ? "Signed up" : <span className="muted">Not yet</span>}</td>
                    <td className="muted">{fmtAgo(i.created_at)}</td>
                    <td className="cell-actions">
                      <button className="btn ghost small" disabled={remove.isPending} onClick={() => remove.mutate(i.id)}>Remove</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
