from __future__ import annotations

import copy
from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path
from typing import Any

TZ_SP = timezone(timedelta(hours=-3))


class DomainError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class RoomManager:
    def __init__(self, base_dir: Path | None = None):
        if base_dir is None:
            # Workspace root is two levels up from servidor-mcp/domain.py or parent of dados
            base_dir = Path(__file__).resolve().parent.parent

        self.base_dir = base_dir
        self.dados_dir = self.base_dir / "dados"

        with open(self.dados_dir / "salas.json", "r", encoding="utf-8") as f:
            self.salas: list[dict[str, Any]] = json.load(f)
        self.salas_by_id = {s["id"]: s for s in self.salas}

        with open(self.dados_dir / "reservas.json", "r", encoding="utf-8") as f:
            raw_reservas: list[dict[str, Any]] = json.load(f)
        self.reservas: list[dict[str, Any]] = copy.deepcopy(raw_reservas)

        with open(self.dados_dir / "politica-de-uso.md", "r", encoding="utf-8") as f:
            self.politica_texto = f.read()

        # Extract politica version from first line (e.g. "versao: 2026-11-01")
        primeira_linha = self.politica_texto.splitlines()[0].strip()
        self.politica_versao = primeira_linha.replace("versao:", "").strip()

        # Counter for new reservations
        max_num = 0
        for r in self.reservas:
            rid = r.get("id", "")
            if rid.startswith("res-"):
                try:
                    num = int(rid[4:])
                    if num > max_num:
                        max_num = num
                except ValueError:
                    pass
        self._counter = max_num

    def next_reserva_id(self) -> str:
        self._counter += 1
        return f"res-{self._counter:04d}"

    def parse_dt(self, dt_str: str) -> datetime:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_SP)
        return dt

    def validate_interval(self, sala_id: str, inicio_str: str, fim_str: str) -> tuple[datetime, datetime]:
        if sala_id not in self.salas_by_id:
            raise DomainError(f"Sala inexistente: {sala_id}")

        try:
            inicio = self.parse_dt(inicio_str)
            fim = self.parse_dt(fim_str)
        except Exception:
            raise DomainError("Intervalo invalido: formato de data invalido")

        if fim <= inicio:
            raise DomainError("Intervalo invalido: fim deve ser posterior a inicio")

        inicio_sp = inicio.astimezone(TZ_SP)
        fim_sp = fim.astimezone(TZ_SP)

        # Reservas somente entre 08:00 e 20:00, horario de Sao Paulo (-03:00)
        # Check start and end within window and on same date
        if (
            inicio_sp.date() != fim_sp.date()
            or inicio_sp.time() < time(8, 0)
            or inicio_sp.time() > time(20, 0)
            or fim_sp.time() < time(8, 0)
            or fim_sp.time() > time(20, 0)
        ):
            raise DomainError("Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00")

        # Duracao maxima de 2 horas
        if (fim - inicio) > timedelta(hours=2):
            raise DomainError("Duracao acima do limite: a politica permite no maximo 2 horas")

        return inicio, fim

    def find_conflicts(self, sala_id: str, inicio: datetime, fim: datetime) -> list[dict[str, Any]]:
        conflitos = []
        for r in self.reservas:
            if r["sala"] == sala_id:
                r_inicio = self.parse_dt(r["inicio"])
                r_fim = self.parse_dt(r["fim"])
                # Overlap test: max(inicio, r_inicio) < min(fim, r_fim)
                if max(inicio, r_inicio) < min(fim, r_fim):
                    conflitos.append(r)
        return conflitos

    def find_alternatives(self, requested_sala_id: str, inicio: datetime, fim: datetime) -> list[str]:
        target_room = self.salas_by_id[requested_sala_id]
        min_cap = target_room["capacidade"]

        candidates = []
        for s in self.salas:
            if s["id"] == requested_sala_id:
                continue
            if s["capacidade"] < min_cap:
                continue
            # Check if candidate room is free in [inicio, fim)
            if not self.find_conflicts(s["id"], inicio, fim):
                candidates.append(s)

        # Ordenadas por capacidade crescente e, em empate, por id em ordem alfabetica
        candidates.sort(key=lambda s: (s["capacidade"], s["id"]))
        # No maximo tres
        return [s["id"] for s in candidates[:3]]

    def add_reserva(self, sala_id: str, inicio_str: str, fim_str: str, responsavel: str) -> dict[str, Any]:
        res_id = self.next_reserva_id()
        nova = {
            "id": res_id,
            "sala": sala_id,
            "inicio": inicio_str,
            "fim": fim_str,
            "responsavel": responsavel,
        }
        self.reservas.append(nova)
        return nova
