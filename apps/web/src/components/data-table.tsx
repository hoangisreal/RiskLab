"use client";
import { useState } from "react";
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
// The server owns search/filter/pagination; sorting is explicitly limited to this page.
export function DataTable<T>({
  data,
  columns,
}: {
  data: T[];
  columns: ColumnDef<T>[];
}) {
  const [sorting, setSorting] = useState<SortingState>([]);
  // TanStack returns mutable closures; keep this component outside React Compiler memoization.
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });
  return (
    <div className="table-scroll">
      <p className="sort-note">
        Sắp xếp chỉ áp dụng cho trang hiện tại; bộ lọc và tìm kiếm do máy chủ xử
        lý.
      </p>
      <table>
        <thead>
          {table.getHeaderGroups().map((g) => (
            <tr key={g.id}>
              {g.headers.map((h) => (
                <th
                  key={h.id}
                  aria-sort={
                    h.column.getIsSorted() === "asc"
                      ? "ascending"
                      : h.column.getIsSorted() === "desc"
                        ? "descending"
                        : "none"
                  }
                >
                  {h.column.getCanSort() ? (
                    <button onClick={h.column.getToggleSortingHandler()}>
                      {flexRender(h.column.columnDef.header, h.getContext())} ↕
                    </button>
                  ) : (
                    flexRender(h.column.columnDef.header, h.getContext())
                  )}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.length === 0 && (
            <tr>
              <td colSpan={columns.length}>Không có kết quả phù hợp.</td>
            </tr>
          )}
          {table.getRowModel().rows.map((r) => (
            <tr key={r.id}>
              {r.getVisibleCells().map((c) => (
                <td
                  key={c.id}
                  data-label={
                    typeof c.column.columnDef.header === "string"
                      ? c.column.columnDef.header
                      : ""
                  }
                >
                  {flexRender(c.column.columnDef.cell, c.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
