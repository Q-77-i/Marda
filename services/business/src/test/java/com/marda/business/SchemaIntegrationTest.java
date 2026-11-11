package com.marda.business;

import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.Test;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.HashSet;
import java.util.Set;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * 数据库契约测试：Flyway 在空 PG 上建出四张表，并守住唯一键 / 检查约束。
 *
 * <p>这是 schema 的公开行为，不测实现细节；后续 API 用例仍走更高接缝。
 */
@Testcontainers
class SchemaIntegrationTest {

    @Container
    static final PostgreSQLContainer<?> POSTGRES =
            new PostgreSQLContainer<>("postgres:16-alpine")
                    .withDatabaseName("marda_business")
                    .withUsername("marda")
                    .withPassword("marda");

    @Test
    void migrations_create_four_tables_and_guard_constraints() throws Exception {
        Flyway.configure()
                .dataSource(POSTGRES.getJdbcUrl(), POSTGRES.getUsername(), POSTGRES.getPassword())
                .locations("classpath:db/migration")
                .load()
                .migrate();

        try (Connection connection = DriverManager.getConnection(
                POSTGRES.getJdbcUrl(), POSTGRES.getUsername(), POSTGRES.getPassword())) {
            assertThat(tableNames(connection)).contains(
                    "learning_plan", "learning_task", "notification", "learning_plan_skip");

            UUID planId = UUID.randomUUID();
            insertPlan(connection, planId, "iv-1");
            assertThatThrownBy(() -> insertPlan(connection, UUID.randomUUID(), "iv-1"))
                    .isInstanceOf(SQLException.class)
                    .hasMessageContaining("uq_learning_plan_source_interview");

            assertThatThrownBy(() -> insertTask(connection, planId, 0))
                    .isInstanceOf(SQLException.class)
                    .hasMessageContaining("ck_learning_task_priority");

            insertTask(connection, planId, 1);
            assertThatThrownBy(() -> insertTask(connection, planId, 2))
                    .isInstanceOf(SQLException.class)
                    .hasMessageContaining("uq_learning_task_plan_domain");
        }
    }

    private static Set<String> tableNames(Connection connection) throws SQLException {
        Set<String> names = new HashSet<>();
        try (PreparedStatement statement = connection.prepareStatement(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'");
             ResultSet resultSet = statement.executeQuery()) {
            while (resultSet.next()) {
                names.add(resultSet.getString(1));
            }
        }
        return names;
    }

    private static void insertPlan(Connection connection, UUID id, String sourceInterviewId)
            throws SQLException {
        try (PreparedStatement statement = connection.prepareStatement("""
                INSERT INTO learning_plan
                    (id, user_id, source_interview_id, goal, status, version,
                     idempotency_key, request_hash, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """)) {
            Timestamp now = Timestamp.from(Instant.parse("2026-11-11T00:00:00Z"));
            statement.setObject(1, id);
            statement.setString(2, "u1");
            statement.setString(3, sourceInterviewId);
            statement.setString(4, "goal");
            statement.setString(5, "active");
            statement.setLong(6, 1);
            statement.setString(7, sourceInterviewId);
            statement.setString(8, "hash");
            statement.setTimestamp(9, now);
            statement.setTimestamp(10, now);
            statement.executeUpdate();
        }
    }

    private static void insertTask(Connection connection, UUID planId, int priority)
            throws SQLException {
        try (PreparedStatement statement = connection.prepareStatement("""
                INSERT INTO learning_task
                    (id, plan_id, domain, title, status, due_at, priority, version,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """)) {
            Timestamp now = Timestamp.from(Instant.parse("2026-11-11T00:00:00Z"));
            statement.setObject(1, UUID.randomUUID());
            statement.setObject(2, planId);
            statement.setString(3, "rag");
            statement.setString(4, "title");
            statement.setString(5, "todo");
            statement.setTimestamp(6, now);
            statement.setInt(7, priority);
            statement.setLong(8, 1);
            statement.setTimestamp(9, now);
            statement.setTimestamp(10, now);
            statement.executeUpdate();
        }
    }
}
