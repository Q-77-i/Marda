# P5-J3 业务服务镜像：Maven 构建 + JRE 21 运行。
# 构建上下文 = services/business；target/ 由 .dockerignore 排除。
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /app
COPY pom.xml .
RUN mvn -B -q -DskipTests dependency:go-offline
COPY src ./src
RUN mvn -B -q -DskipTests package

FROM eclipse-temurin:21-jre-alpine
WORKDIR /app
COPY --from=build /app/target/*.jar app.jar
EXPOSE 8081
ENV TZ=Asia/Shanghai
ENV JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=75"
HEALTHCHECK --interval=10s --timeout=5s --retries=12 --start-period=30s \
    CMD wget -qO- http://127.0.0.1:8081/actuator/health/readiness | grep -q UP || exit 1
ENTRYPOINT ["java", "-jar", "app.jar"]
