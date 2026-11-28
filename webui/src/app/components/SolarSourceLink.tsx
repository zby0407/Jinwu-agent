export function SolarSourceLink({ href, children }: { href: string; children: string }) {
  return <a href={href} target="_blank" rel="noreferrer" className="text-sky-800 underline decoration-sky-300 underline-offset-2 hover:text-sky-950">{children}</a>;
}
