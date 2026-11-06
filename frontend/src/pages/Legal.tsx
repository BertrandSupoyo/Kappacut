import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { usePublicConfigQuery, type PublicConfig } from "@/api/config";
import { SiteFooter } from "@/components/SiteFooter";
import { Wordmark } from "@/components/Wordmark";

/* The Terms, Privacy Policy and copyright pages. When this text changes, change TERMS_VERSION in
   app/config.py too: it's the date shown here, and what each new account records having accepted.
   Names, addresses and day counts come from the server, so they match the running deployment. */

function missing(value: string, setting: string) {
  // a public deploy refuses to boot without these (Settings.check_production_ready)
  return value || `[${setting} is not set]`;
}

function fmtDate(version: string) {
  const d = new Date(`${version}T00:00:00Z`);
  return d.toLocaleDateString("en", { year: "numeric", month: "long", day: "numeric", timeZone: "UTC" });
}

function Mail({ to }: { to: string }) {
  return to ? <a href={`mailto:${to}`}>{to}</a> : <>[SUPPORT_EMAIL is not set]</>;
}

function Page({ title, children }: { title: string; children: (cfg: PublicConfig) => ReactNode }) {
  const { data: cfg, isError } = usePublicConfigQuery();
  return (
    <div className="legal-page">
      <header className="wrap" style={{ padding: "var(--sp-4) 0" }}>
        <Wordmark />
      </header>
      <main className="wrap legal">
        <h1>{title}</h1>
        {isError ? (
          <p role="alert">This page couldn't load. Please try again in a moment.</p>
        ) : !cfg ? (
          <div className="spin" />
        ) : (
          <>
            <p className="muted">Last updated {fmtDate(cfg.legal.version)}</p>
            {!cfg.legal.reviewed && (
              <p className="legal-draft" role="note">
                Draft: this text hasn't been checked by a lawyer yet, and it may change.
              </p>
            )}
            {children(cfg)}
          </>
        )}
        <p style={{ marginTop: "var(--sp-6)" }}>
          <Link to="/">← Back to Kappacut</Link>
        </p>
      </main>
      <SiteFooter />
    </div>
  );
}

export function Terms() {
  return (
    <Page title="Terms of Use">
      {(cfg) => (
        <>
          <h2>1. Who we are</h2>
          <p>
            Kappacut ("we") is run by {missing(cfg.legal.name, "LEGAL_NAME")},{" "}
            {missing(cfg.legal.address, "LEGAL_ADDRESS")}. It transcribes videos you upload, suggests short clips from
            them, and renders the clips you choose. By creating an account you agree to these terms.
          </p>

          <h2>2. Your account</h2>
          <p>
            You must be at least 16 to use Kappacut. Keep your password safe. You're responsible for what happens under
            your account.
          </p>

          <h2>3. The free plan and its limits</h2>
          <p>
            Kappacut is currently free, with the limits shown on your <Link to="/account">Account</Link> page. We may
            change the limits or features. If we ever start charging, we'll tell you first, and you won't pay anything
            you haven't agreed to.
          </p>

          <h2>4. English only, for now</h2>
          <p>
            Kappacut is built for English-speaking videos. A video in another language may be stopped before analysis,
            and if you choose to analyze it anyway, the clips and captions are likely to be poor.
          </p>

          <h2>5. Your content</h2>
          <p>
            You keep all your rights to what you upload and make. You give us permission to store, copy and process it
            only to run Kappacut for you, which includes sending parts of it to the companies listed in our{" "}
            <Link to="/privacy">Privacy Policy</Link>. That permission ends when you delete the content, apart from the
            short time it stays in backups.
          </p>

          <h2>6. What you can't do</h2>
          <ul>
            <li>Upload anything you don't own or don't have permission to use.</li>
            <li>Upload anything illegal, including any sexual content involving a minor, which we report to the authorities.</li>
            <li>Use Kappacut to harass, defame or impersonate someone.</li>
            <li>Try to break into Kappacut, overload it, or get around its limits, for example with several accounts.</li>
          </ul>

          <h2>7. Copyright</h2>
          <p>
            We act on copyright notices, and we close the accounts of people who repeatedly infringe. See our{" "}
            <Link to="/copyright">copyright page</Link>.
          </p>

          <h2>8. AI suggestions can be wrong</h2>
          <p>
            Clip picks, scores, titles and captions are generated automatically, and they can be wrong or miss things.
            Check your clips before you publish them. You're responsible for what you publish.
          </p>

          <h2>9. Keep your own copies</h2>
          <p>
            {cfg.retention.delete_days > 0 ? (
              <>
                A project you haven't opened for {cfg.retention.delete_days} days is deleted, and we email you{" "}
                {cfg.retention.delete_days - cfg.retention.warn_days} days before that.{" "}
              </>
            ) : null}
            We can't promise your files will never be lost, so keep copies of anything that matters to you.
          </p>

          <h2>10. Ending your account</h2>
          <p>
            You can delete your account at any time on your <Link to="/account">Account</Link> page. We may suspend or
            close an account that breaks these terms, and we'll tell you why unless the law or safety stops us.
          </p>

          <h2>11. No guarantees</h2>
          <p>
            Kappacut is provided as it is, without any promise that it will always work or suit your purpose. As far as
            the law allows, we aren't liable for indirect losses, lost profits or lost data. Nothing here limits a
            liability or a consumer right that the law doesn't allow us to limit.
          </p>

          <h2>12. Changes</h2>
          <p>
            We may update these terms. If a change matters, we'll tell you by email or on the site before it takes
            effect. Continuing to use Kappacut after that means you accept the change.
          </p>

          <h2>13. The law that applies</h2>
          <p>
            These terms are governed by the laws of {missing(cfg.legal.jurisdiction, "LEGAL_JURISDICTION")}. If you're a
            consumer, you keep any protections that the law of the country you live in doesn't allow a contract to
            remove.
          </p>

          <h2>14. Contact</h2>
          <p>
            <Mail to={cfg.support_email} />
          </p>
        </>
      )}
    </Page>
  );
}

export function Privacy() {
  return (
    <Page title="Privacy Policy">
      {(cfg) => (
        <>
          <h2>Who's responsible for your data</h2>
          <p>
            {missing(cfg.legal.name, "LEGAL_NAME")}, {missing(cfg.legal.address, "LEGAL_ADDRESS")}. Contact:{" "}
            <Mail to={cfg.support_email} />.
            {cfg.legal.eu_representative && <> Our representative in the EU: {cfg.legal.eu_representative}.</>}
          </p>

          <h2>What we collect</h2>
          <ul>
            <li><b>Your account:</b> your email address, a scrambled form of your password, and the date you accepted our terms.</li>
            <li><b>What you upload:</b> your videos and any sound effects.</li>
            <li><b>What we make from it:</b> transcripts, suggested clips and their scores, your edits, rendered clips, thumbnails, preview frames and waveforms.</li>
            <li><b>Your usage:</b> minutes transcribed, clips rendered, bytes uploaded, the jobs you've run and how many messages you've sent the AI agent and AI editor, which is how your limits work.</li>
            <li><b>What you type to the AI agent or AI editor:</b> we send it to our AI provider to get an answer, and we don't store it. The changes it makes are saved like any other edit.</li>
            <li><b>Technical data:</b> your IP address, which we use to limit repeated requests and hold for up to an hour. Our server logs can contain your email address and details of your jobs, and they're overwritten as they fill up.</li>
          </ul>

          <h2>Why we use it, and our legal basis</h2>
          <ul>
            <li><b>To run Kappacut for you</b>, including your account, its limits, and emails about it. Basis: our contract with you.</li>
            <li><b>To keep Kappacut secure</b> and stop abuse. Basis: our legitimate interest in running a safe service.</li>
            <li><b>To improve the clip finder</b>, by measuring which suggested clips people keep, trim and render. Basis: our legitimate interest in making it better.</li>
            <li><b>To handle legal notices</b> such as copyright complaints. Basis: our legal obligations and legitimate interests.</li>
          </ul>
          <p>
            We don't use your videos or transcripts to train AI models. The only videos used to tune the clip finder are
            ones our own team uploads and marks by hand. We don't sell your personal information or share it for
            advertising.
          </p>

          <h2>Who else handles your data</h2>
          <div className="table-wrap">
            <table className="admin-table legal-table">
              <thead>
                <tr><th>Company</th><th>What it gets</th></tr>
              </thead>
              <tbody>
                {cfg.processors.map((p) => (
                  <tr key={p.name}><td>{p.name}</td><td>{p.gets}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          <p>
            Some of these companies are in the United States, so your data may be processed outside your country. Where
            the law requires a safeguard for that, we rely on the ones they offer, such as standard contractual clauses.
            We may also disclose data when the law requires it.
          </p>

          <h2>How long we keep it</h2>
          <ul>
            {cfg.retention.delete_days > 0 && (
              <li>A project you haven't opened for {cfg.retention.delete_days} days is deleted, and we warn you by email first.</li>
            )}
            {cfg.retention.unverified_days > 0 && (
              <li>An account whose email address is never confirmed is deleted after {cfg.retention.unverified_days} days.</li>
            )}
            <li>
              When you delete your account, your files and records are removed right away. Copies in our database backups
              last up to {cfg.retention.backup_days} more days, and a part-finished upload up to one more day.
            </li>
            <li>We keep a record of copyright removals, including the email address involved, for as long as we may need it to handle a claim or apply our repeat-infringer policy.</li>
          </ul>

          <h2>Cookies</h2>
          <p>
            We use two cookies, both needed for Kappacut to work: <code>cfa</code> keeps you signed in, and{" "}
            <code>csrftoken</code> protects your account from forged requests. We don't use analytics or advertising
            cookies.
          </p>

          <h2>Your rights</h2>
          <ul>
            <li><b>Get a copy of your data:</b> use Download my data on your <Link to="/account">Account</Link> page.</li>
            <li><b>Delete your data:</b> delete a project, or your whole account, in the app.</li>
            <li><b>Correct your data, or object to or restrict how we use it:</b> email <Mail to={cfg.support_email} />.</li>
          </ul>
          <p>
            Depending on where you live, you may also have the right to complain to your data protection authority. If
            you can't sign in, email us and we'll help.
          </p>

          <h2>Children</h2>
          <p>
            Kappacut isn't for anyone under 16. If you believe a child has given us their data, email us and we'll delete
            it.
          </p>

          <h2>Changes</h2>
          <p>If we change this policy in a way that matters, we'll tell you by email or on the site.</p>
        </>
      )}
    </Page>
  );
}

export function Copyright() {
  return (
    <Page title="Copyright and illegal content">
      {(cfg) => {
        const agent = cfg.copyright_email;
        return (
          <>
            <p>
              Kappacut users must only upload material they own or have permission to use. If you think something on
              Kappacut infringes your rights, or is illegal in another way, tell us and we'll act on it.
            </p>

            <h2>Where to send a notice</h2>
            <p>
              {missing(cfg.legal.name, "LEGAL_NAME")}, {missing(cfg.legal.address, "LEGAL_ADDRESS")}
              <br />
              Email: <Mail to={agent} />
            </p>

            <h2>What a copyright notice needs</h2>
            <ol>
              <li>Your name, postal address, phone number and email address.</li>
              <li>The copyrighted work you say is being infringed.</li>
              <li>
                What on Kappacut you're complaining about, in as much detail as you have. Files on Kappacut are private to
                each account, so tell us what you've seen: a link, a project or file name, or the account's email address.
              </li>
              <li>A statement that you believe in good faith that this use isn't authorized by the owner, its agent or the law.</li>
              <li>A statement that the information in your notice is accurate and, under penalty of perjury, that you own the right or are authorized to act for the owner.</li>
              <li>Your physical or electronic signature.</li>
            </ol>

            <h2>What happens next</h2>
            <p>
              If your notice is complete, we remove the project and its clips, and tell the account's owner. Knowingly
              making a false claim can make you liable for damages.
            </p>

            <h2>If your project was removed</h2>
            <p>If you think this was a mistake, or you have permission to use the material, send a counter-notice to the address above with:</p>
            <ol>
              <li>Your name, address, phone number and signature.</li>
              <li>The project that was removed.</li>
              <li>A statement, under penalty of perjury, that you believe in good faith it was removed by mistake or misidentification.</li>
              <li>Your agreement to the jurisdiction of the courts for your address (or, outside the United States, any court where we can be found), and to accept service from the person who sent the notice.</li>
            </ol>
            <p>
              We pass your counter-notice to the person who complained. Unless they tell us they've started a court action
              within 10 to 14 business days, we can restore your access, but we delete the files when we remove a project, so
              you may need to upload the video again.
            </p>

            <h2>Repeat infringers</h2>
            <p>We close the accounts of people who repeatedly infringe copyright, and we keep a record of removals for this purpose.</p>

            <h2>Other illegal content</h2>
            <p>
              Send the same address a description of the content, where you found it, and why you believe it's illegal. We
              review reports and tell you what we decided. This is also our contact point for authorities.
            </p>
          </>
        );
      }}
    </Page>
  );
}
