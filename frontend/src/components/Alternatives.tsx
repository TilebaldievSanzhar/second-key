// «Где дешевле» по главным позициям вопроса: ваши же поставщики за последние три месяца и госзакупки.
// Не «вас обманывают», а «вот где дешевле» — то, с чем можно идти к поставщику или снабженцу.
import { useQuery } from "@tanstack/react-query";
import { api, unwrap, type AltGroup } from "../api/client";
import { PURCHASES, count, date, dayMonth, int, signedShare, unit as unitText } from "../format";
import { ErrorNote, Skeleton } from "./State";

export function Alternatives({ findingId }: { findingId: string }) {
  const q = useQuery({
    queryKey: ["alternatives", findingId],
    queryFn: () => unwrap(api.GET("/api/findings/{finding_id}/alternatives", { params: { path: { finding_id: findingId } } })),
  });
  if (q.isPending) return <Skeleton lines={3} label="Ищем, где дешевле…" />;
  if (q.isError) return <ErrorNote error={q.error} retry={() => q.refetch()} />;
  const groups = q.data.groups.filter((g) => g.options.length || g.market);
  if (!groups.length) return null;
  return (
    <section className="block alts" aria-label="Где дешевле">
      <div className="block-head">
        <h2>Где дешевле</h2>
        <span className="muted small">
          медиана цен с {dayMonth(q.data.window_start)} по {date(q.data.window_end)}
        </span>
      </div>
      {groups.map((g) => (
        <AltTable key={g.sku} g={g} />
      ))}
      <p className="muted small">
        Цены — из ваших закупок и госзакупок КР, с НДС, за {unitText(groups[0]!.unit) || "единицу"}. Условия поставки и
        отсрочка у поставщиков могут отличаться — уточните их перед заказом.
      </p>
    </section>
  );
}

function AltTable({ g }: { g: AltGroup }) {
  const u = unitText(g.unit);
  const cheaper = g.options.filter((o) => o.delta !== null && Number(o.delta) < 0).length;
  return (
    <div className="alt">
      <h3 className="alt-title">
        {g.label}
        <span className="muted small">
          {cheaper ? ` · дешевле у ${cheaper} ${cheaper === 1 ? "поставщика" : "поставщиков"}` : " · дешевле в ваших закупках не нашлось"}
        </span>
      </h3>
      <div className="table-wrap">
        <table className="table table-tight alt-table">
          <thead>
            <tr>
              <th>Где</th>
              <th className="r">Цена, сом/{u || "ед."}</th>
              <th className="r">Разница</th>
              <th className="desktop-cell">Основание</th>
            </tr>
          </thead>
          <tbody>
            {g.current && (
              <tr className="alt-current">
                <td>
                  {g.supplier}
                  <span className="alt-mark">в вопросе</span>
                </td>
                <td className="r num">{int(g.current.price)}</td>
                <td className="r muted">—</td>
                <td className="desktop-cell muted small">{basis(g.current)}</td>
              </tr>
            )}
            {g.options.map((o) => (
              <tr key={o.supplier}>
                <td>{o.supplier}</td>
                <td className="r num">{int(o.price)}</td>
                <td className={`r num${o.delta !== null && Number(o.delta) < 0 ? " alt-cheaper" : ""}`}>{o.delta === null ? "—" : signedShare(o.delta)}</td>
                <td className="desktop-cell muted small">{basis(o)}</td>
              </tr>
            ))}
            {g.market && (
              <tr className="alt-market">
                <td>Рынок · госзакупки КР</td>
                <td className="r num">{int(g.market.price)}</td>
                <td className={`r num${g.market.delta !== null && Number(g.market.delta) < 0 ? " alt-cheaper" : ""}`}>
                  {g.market.delta === null ? "—" : signedShare(g.market.delta)}
                </td>
                <td className="desktop-cell muted small">
                  {g.market.points} цен{g.market.recent ? "" : `, последняя ${date(g.market.last_date)}`}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const basis = (q: { lines: number; last_date: string; recent: boolean }) =>
  q.recent ? `${count(q.lines, PURCHASES)}, последняя ${dayMonth(q.last_date)}` : `давно не покупали: ${date(q.last_date)}`;
