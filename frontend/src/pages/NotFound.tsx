import { Link } from "react-router";
import { Empty } from "../components/State";

export function NotFoundPage() {
  return (
    <section className="page">
      <Empty
        title="Такой страницы нет"
        action={
          <Link className="btn btn-primary" to="/">
            На сводку
          </Link>
        }
      >
        Возможно, ссылка устарела или её скопировали не целиком.
      </Empty>
    </section>
  );
}
